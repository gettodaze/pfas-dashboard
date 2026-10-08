import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from .batches import Batches, validate_positive
from .inputs import discover, dry_input, ram_reports, validate_references
from .persistence import Store, now
from .preparation import digest, pseudo_names
from .processes import evidence, execute, tail
from .runtimes import GIB, Runtimes, memory_ceiling

logger = logging.getLogger("uvicorn.error")


class Manager:
    def __init__(self, config, candidates):
        self.config = config
        config.artifacts.mkdir(parents=True, exist_ok=True)
        self.store = Store(config.artifacts / "tasks.sqlite")
        self.candidates = candidates
        self.chem_env = {
            **os.environ,
            "PATH": str(Path(config.prepare_python).absolute().parent)
            + os.pathsep
            + os.environ["PATH"],
        }
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.guard = threading.RLock()
        self.running = {}
        self.previews = {}
        self.runtimes = Runtimes(config)
        self.batches = Batches(self)
        self.settings = self.store.settings(
            {
                "paused": False,
                "concurrency": 1,
                "memory_bytes": min(8 * GIB, memory_ceiling() * 9 // 10),
                "cpus": min(
                    4,
                    len(os.sched_getaffinity(0))
                    if hasattr(os, "sched_getaffinity")
                    else os.cpu_count() or 1,
                ),
            }
        )
        self.settings["memory_bytes"] = min(
            self.settings["memory_bytes"], memory_ceiling() * 9 // 10
        )
        available_cpus = (
            len(os.sched_getaffinity(0))
            if hasattr(os, "sched_getaffinity")
            else os.cpu_count() or 1
        )
        self.settings["cpus"] = min(self.settings["cpus"], available_cpus)
        self.thread = threading.Thread(target=self.worker, daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        with self.guard:
            self.stop.set()
            for _, cancel, _ in self.running.values():
                cancel.set()
            self.wake.set()
        if self.thread.is_alive():
            self.thread.join()
        for task in self.store.tasks():
            if task["status"] == "running":
                self.store.update(
                    task["id"],
                    status="interrupted",
                    ended=now(),
                    error="Server stopped; retry explicitly",
                )

    def executable(self, name):
        path = shutil.which(name)
        if not path:
            raise ValueError(f"Executable unavailable: {name}")
        return path

    def preflight(self, candidate):
        probe = subprocess.run(
            [
                self.config.prepare_python,
                "-c",
                "import shutil; import rdkit, pymatgen.core, ase, openbabel; assert shutil.which('obabel'); assert shutil.which('cif2cell')",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=self.chem_env,
        )
        if probe.returncode:
            raise ValueError(
                f"Preparation interpreter {self.config.prepare_python} requires RDKit, pymatgen, ASE, Open Babel and cif2cell. "
                "Start with uv run --no-default-groups --group web --group preparation python -m dashboard, or set PFAS_CHEM_PYTHON to a chemistry interpreter. Details: "
                + probe.stderr[-2000:]
            )
        # RDKit runs in the configured interpreter, keeping the web environment minimal.
        result = subprocess.run(
            [
                self.config.prepare_python,
                "-c",
                "from rdkit import Chem; import sys; m=Chem.MolFromSmiles(sys.argv[1]); assert m is not None; print(' '.join(sorted({a.GetSymbol() for a in m.GetAtoms()} | {'H','C','O','F'})))",
                candidate["smiles"],
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=self.chem_env,
        )
        if result.returncode:
            raise ValueError("Invalid representative SMILES")
        missing = [
            s + ".UPF"
            for s in result.stdout.split()
            if not (self.config.pseudos / (s + ".UPF")).is_file()
        ]
        if missing:
            raise ValueError("Missing pseudopotentials: " + ", ".join(missing))

    def input(self, candidate, system, tasks=None):
        owner = "tfa" if system == "tfa" else candidate
        for task in self.store.prepared(candidate, system) if tasks is None else tasks:
            if (
                task["kind"] == "prepare"
                and task["status"] == "succeeded"
                and (task["candidate"] == owner or system == "tfa")
            ):
                path = self.config.artifacts / task["id"] / (system + ".in")
                if path.exists():
                    return path
        raise ValueError("Prepare inputs first")

    def versions(self):
        return [
            {
                "input_id": identifier,
                "candidate": owner,
                "system": system,
                "label": path.name,
            }
            for identifier, (owner, system, path) in discover(
                self.config.qe_inputs, self.candidates
            ).items()
        ]

    def preview(
        self,
        candidate,
        system,
        processes,
        target,
        runtime="native",
        input_id=None,
        kind="qe",
        issue_token=True,
        _tasks=None,
        _pseudo_hashes=None,
    ):
        if runtime not in ("native", "apptainer"):
            raise ValueError("Unknown runtime")
        if target != "local":
            raise NotImplementedError(
                "Slurm and Slurm array execution are not implemented"
            )
        if type(processes) is not int or processes < 1:
            raise ValueError("Process count must be a positive integer")
        if system not in ("tfa", "candidate", "complex"):
            raise ValueError("Unknown system")
        if candidate not in self.candidates or (candidate == "tfa" and system != "tfa"):
            raise ValueError("Unknown candidate/system")
        if kind not in ("qe", "estimate_ram"):
            raise ValueError("Unknown preview task")
        if input_id is None:
            path = self.input(candidate, system, tasks=_tasks)
            label = "Prepared default"
        else:
            entry = discover(self.config.qe_inputs, self.candidates).get(input_id)
            if not entry or entry[:2] != (
                "tfa" if system == "tfa" else candidate,
                system,
            ):
                raise ValueError("Invalid input identifier for candidate/system")
            path = entry[2]
            label = path.name
        input_bytes = path.read_bytes()
        text = input_bytes.decode()
        validate_references(text)
        source_hash = hashlib.sha256(input_bytes).hexdigest()
        source_text = text
        if kind == "estimate_ram":
            text = dry_input(text)
        names = pseudo_names(text)
        missing = [n for n in names if not (self.config.pseudos / n).is_file()]
        if missing:
            raise ValueError("Missing pseudopotentials: " + ", ".join(missing))
        hashes = {}
        for name in names:
            path = self.config.pseudos / name
            if _pseudo_hashes is None:
                hashes[name] = digest(path)
            else:
                stat = path.stat()
                key = (path, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                if key not in _pseudo_hashes:
                    _pseudo_hashes[key] = digest(path)
                hashes[name] = _pseudo_hashes[key]
        pw = "pw.x" if runtime == "apptainer" else self.executable(self.config.pw)
        command = (
            [pw, "-in", "input.in"]
            if processes == 1
            else [
                "mpirun"
                if runtime == "apptainer"
                else self.executable(self.config.mpi),
                "-np",
                str(processes),
                pw,
                "-in",
                "input.in",
            ]
        )
        from .geometry import geometry

        try:
            initial_geometry = geometry(source_text)
        except (ValueError, IndexError) as error:
            initial_geometry = {"error": str(error)}
        result = {
            "input_id": input_id,
            "version": label,
            "source_hash": source_hash,
            "executed_input_hash": hashlib.sha256(text.encode()).hexdigest(),
            "geometry": initial_geometry,
            "image_hash": self.runtimes.image_identity()
            if runtime == "apptainer"
            else None,
            "command": command,
            "input": text,
            "input_hash": hashlib.sha256(text.encode()).hexdigest(),
            "pseudopotentials": hashes,
        }

        if issue_token:
            token = uuid.uuid4().hex
            with self.guard:
                if len(self.previews) >= 2048:
                    self.previews.pop(next(iter(self.previews)))
                self.previews[token] = (
                    (candidate, system, processes, target, runtime, input_id, kind),
                    result.copy(),
                )
            result["preview_id"] = token
        return result

    def queue(
        self,
        kind,
        candidate,
        system="candidate",
        processes=1,
        target="local",
        timeout=None,
        expected_hash=None,
        runtime="native",
        memory_gib=4,
        batch_id=None,
        position=None,
        checked_preflight=False,
        expected_pseudos=None,
        input_id=None,
        preview_id=None,
    ):
        if target != "local":
            raise NotImplementedError(
                "Slurm and Slurm array execution are not implemented"
            )
        if candidate not in self.candidates:
            raise ValueError("Unknown candidate")
        if kind not in ("diagram", "prepare", "qe", "estimate_ram"):
            raise ValueError("Unknown task")
        if timeout is not None and (
            not isinstance(timeout, (float, int)) or timeout <= 0
        ):
            raise ValueError("Timeout must be positive")
        if kind not in ("qe", "estimate_ram"):
            processes = 1
            system = "tfa" if candidate == "tfa" else "candidate"
        timeout = (
            timeout
            if timeout is not None
            else self.config.prepare_timeout
            if kind == "prepare"
            else 60
            if kind == "diagram"
            else 120
            if kind == "estimate_ram"
            else None
        )
        resources = self.resources(
            runtime,
            processes if kind in ("qe", "estimate_ram") else 1,
            memory_gib,
            timeout,
        )
        image_hash = self.runtimes.verify() if runtime == "apptainer" else None
        with self.guard:
            existing = self.store.active(kind, candidate, system)
            if (
                existing
                and input_id is None
                and kind != "estimate_ram"
                and preview_id is None
            ):
                return existing
            preview = None
            if kind == "prepare" and not checked_preflight:
                try:
                    if runtime == "native":
                        self.preflight(self.candidates[candidate])
                    else:
                        report = self.runtimes.chemistry_probe(
                            runtime,
                            "import shutil; import rdkit,pymatgen.core,ase,openbabel; assert shutil.which('obabel'); assert shutil.which('cif2cell')",
                        )
                        if report.returncode:
                            raise ValueError(
                                "Preparation tools unavailable: "
                                + report.stderr[-1000:]
                            )
                except (ValueError, OSError, subprocess.SubprocessError) as error:
                    logger.warning(
                        "Preparation rejected for cluster %s: %s", candidate, error
                    )
                    raise
            if kind in ("qe", "estimate_ram"):
                preview = self.preview(
                    candidate,
                    system,
                    processes,
                    target,
                    runtime,
                    input_id,
                    kind,
                    issue_token=False,
                )
                if (
                    preview_id is not None
                    or input_id is not None
                    or kind == "estimate_ram"
                ):
                    reviewed = self.previews.get(preview_id)
                    if not reviewed or reviewed != (
                        (candidate, system, processes, target, runtime, input_id, kind),
                        preview,
                    ):
                        raise ValueError(
                            "Input, pseudopotentials, or execution settings changed; preview again"
                        )
                if (
                    preview_id is None
                    and input_id is None
                    and kind == "qe"
                    and expected_pseudos is None
                ):
                    specification = (
                        candidate,
                        system,
                        processes,
                        target,
                        runtime,
                        input_id,
                        kind,
                    )
                    if not any(
                        record == (specification, preview)
                        for record in self.previews.values()
                    ):
                        raise ValueError(
                            "Input or pseudopotentials changed; preview again"
                        )
                if (
                    expected_pseudos is not None
                    and expected_pseudos != preview["pseudopotentials"]
                ):
                    raise ValueError("Pseudopotentials changed; preview again")
                if expected_hash != preview["input_hash"]:
                    raise ValueError(
                        "Input changed or was not previewed; preview again"
                    )
            id = uuid.uuid4().hex
            directory = self.config.artifacts / id
            directory.mkdir()
            task = {
                "id": id,
                "kind": kind,
                "candidate": candidate,
                "system": system,
                "processes": processes,
                "status": "queued",
                "created": now(),
                "started": None,
                "ended": None,
                "timeout": timeout,
                "runtime": runtime,
                "resources": resources,
                "image_hash": image_hash,
                "batch_id": batch_id,
                "position": position,
                "source": {
                    "cid": self.candidates[candidate]["cid"],
                    "smiles": self.candidates[candidate]["smiles"],
                },
                "artifacts": {},
                "error": "",
            }
            if preview:
                (directory / "input.in").write_text(preview.pop("input"))
                (directory / "Outputs").mkdir()
                (directory / "Pseudopotentials").mkdir()
                # Link immutable copies, so later external changes cannot alter a queued run.
                (directory / "pseudo-snapshots").mkdir()
                for name, hash in preview["pseudopotentials"].items():
                    source = self.config.pseudos / name
                    dest = directory / "pseudo-snapshots" / name
                    shutil.copyfile(source, dest)
                    if digest(dest) != hash:
                        raise ValueError("Pseudopotential changed while queuing")
                    (directory / "Pseudopotentials" / name).symlink_to(dest)
                preview.pop("geometry", None)
                task.update(preview)
                if preview_id:
                    self.previews.pop(preview_id, None)
            for name in ("stdout.log", "stderr.log"):
                path = directory / name
                path.touch()
                task["artifacts"][name] = self.store.register(id + "-" + name, path)
            if preview:
                task["artifacts"]["input.in"] = self.store.register(
                    id + "-input.in", directory / "input.in"
                )
            self.store.put(task)
            logger.info(
                "Task %s queued: %s for cluster %s (%s)", id, kind, candidate, system
            )
            self.wake.set()
            return task

    def cancel_task(self, id):
        with self.guard:
            task = self.store.get(id)
            if task["status"] == "queued":
                self.store.update(id, status="canceled", ended=now())
                logger.info("Task %s canceled before starting", id)
            elif task["status"] == "running" and id in self.running:
                logger.info("Task %s stop requested", id)
                self.running[id][1].set()
                self.store.update(id, stop_requested=True)

    def clear_queue(self):
        # Hold admission while canceling the snapshot so waiting jobs cannot start.
        with self.guard:
            tasks = self.store.queued()
            for task in tasks:
                self.cancel_task(task["id"])
            return {"canceled_count": len(tasks)}

    def resources(self, runtime, cpus, memory_gib, timeout):
        if runtime not in ("native", "apptainer"):
            raise ValueError("Unknown runtime")
        validate_positive(cpus, "CPU count", integer=True)
        validate_positive(memory_gib, "Memory limit")
        if timeout is not None:
            validate_positive(timeout, "Timeout")
        memory_bytes = int(memory_gib * GIB)
        if cpus > self.settings["cpus"]:
            raise ValueError("Job CPU count exceeds queue budget")
        if runtime == "apptainer" and memory_bytes > self.settings["memory_bytes"]:
            raise ValueError("Job memory limit exceeds queue budget")
        return {
            "cpus": int(cpus),
            "memory_bytes": memory_bytes,
            "timeout": timeout,
            "hard_memory_limit": runtime == "apptainer",
        }

    def configure(self, values):
        with self.guard:
            settings = {**self.settings, **values}
            validate_positive(settings["concurrency"], "Concurrency", integer=True)
            validate_positive(settings["cpus"], "CPU budget", integer=True)
            validate_positive(settings["memory_bytes"], "Memory budget", integer=True)
            available_cpus = (
                len(os.sched_getaffinity(0))
                if hasattr(os, "sched_getaffinity")
                else os.cpu_count() or 1
            )
            if settings["cpus"] > available_cpus:
                raise ValueError("CPU budget exceeds available CPUs")
            memory_limit = memory_ceiling() * 9 // 10
            if settings["memory_bytes"] > memory_limit:
                raise ValueError(
                    f"Memory budget cannot exceed {memory_limit / GIB:.2f} GiB "
                    f"({memory_limit} bytes; 90% of detected host memory)"
                )
            running_tasks = [value[2] for value in self.running.values()]
            if (
                sum(t["resources"]["cpus"] for t in running_tasks) > settings["cpus"]
                or sum(
                    t["resources"]["memory_bytes"]
                    for t in running_tasks
                    if t.get("runtime") == "apptainer"
                )
                > settings["memory_bytes"]
            ):
                raise ValueError(
                    "New budget is below current running reservations; stop jobs or wait for them to finish"
                )
            for task in self.store.queued():
                resource = task.get(
                    "resources", {"cpus": task["processes"], "memory_bytes": 4 * GIB}
                )
                if resource["cpus"] > settings["cpus"] or (
                    task.get("runtime") == "apptainer"
                    and resource["memory_bytes"] > settings["memory_bytes"]
                ):
                    raise ValueError(
                        "New budget cannot accommodate an existing queued job"
                    )
            self.settings = self.store.set_settings(settings)
            self.wake.set()
            return self.settings

    def queue_data(self):
        tasks = self.store.tasks()
        # No log tails, geometries, or raw input text in queue polling.
        keys = (
            "id",
            "kind",
            "candidate",
            "system",
            "processes",
            "status",
            "stop_requested",
            "created",
            "started",
            "ended",
            "runtime",
            "resources",
            "batch_id",
            "position",
            "error",
            "exit_code",
            "usage",
            "evidence",
            "version",
            "source_hash",
            "executed_input_hash",
            "ram_estimate",
            "artifacts",
        )
        return {
            "settings": self.settings,
            "runtimes": self.runtimes.availability(),
            "batches": self.store.batches(),
            "tasks": [{k: t[k] for k in keys if k in t} for t in tasks],
            "running_count": len(self.running),
        }

    def worker(self):
        while not self.stop.is_set():
            with self.guard:
                for id, (thread, _, _) in list(self.running.items()):
                    if not thread.is_alive():
                        thread.join()
                        del self.running[id]
                if self.stop.is_set():
                    break
                if not self.settings["paused"]:
                    for task in self.store.queued():
                        # Strict FIFO admission; starts follow the reviewed view order.
                        runtime = task.get("runtime", "native")
                        resources = task.get(
                            "resources",
                            {"cpus": task["processes"], "memory_bytes": 4 * GIB},
                        )
                        running_tasks = [value[2] for value in self.running.values()]
                        if runtime == "native" and running_tasks:
                            break
                        if any(
                            t.get("runtime", "native") == "native"
                            for t in running_tasks
                        ):
                            break
                        if len(running_tasks) >= self.settings["concurrency"]:
                            break
                        if (
                            sum(t["resources"]["cpus"] for t in running_tasks)
                            + resources["cpus"]
                            > self.settings["cpus"]
                        ):
                            break
                        if (
                            runtime == "apptainer"
                            and sum(
                                t["resources"]["memory_bytes"] for t in running_tasks
                            )
                            + resources["memory_bytes"]
                            > self.settings["memory_bytes"]
                        ):
                            break
                        cancel = threading.Event()
                        thread = threading.Thread(
                            target=self.execute_attempt,
                            args=(task, cancel),
                            daemon=True,
                        )
                        self.store.update(task["id"], status="running", started=now())
                        self.running[task["id"]] = (thread, cancel, task)
                        thread.start()
                        if runtime == "native":
                            break
            self.wake.wait(0.1)
            self.wake.clear()
        for thread, cancel, _ in list(self.running.values()):
            cancel.set()
            thread.join()
        self.running.clear()

    def execute_attempt(self, task, cancel):
        try:
            self.run(task, cancel)
        except Exception as error:
            # Catch artifact registration/finalization errors too; release the slot.
            logger.exception("Task %s failed during finalization", task["id"])
            self.store.update(
                task["id"], status="failed", error=str(error), ended=now()
            )
        finally:
            self.wake.set()

    def run(self, task, cancel=None):
        cancel = cancel or threading.Event()
        id = task["id"]
        directory = self.config.artifacts / id
        logger.info(
            "Task %s started: %s for cluster %s (%s)",
            id,
            task["kind"],
            task["candidate"],
            task["system"],
        )
        logger.info(
            "Task %s logs: stdout=%s stderr=%s",
            id,
            directory / "stdout.log",
            directory / "stderr.log",
        )
        try:
            candidate = self.candidates[task["candidate"]]
            timeout = task["timeout"]
            if task["kind"] == "diagram":
                command = [
                    self.config.python,
                    str(Path(__file__).with_name("diagram.py")),
                    candidate["smiles"],
                    str(directory / "diagram.png"),
                ]
                timeout = task["timeout"] or 60
            elif task["kind"] == "prepare":
                request = {
                    "candidate": task["candidate"],
                    "smiles": candidate["smiles"],
                    "cid": candidate["cid"],
                    "pseudos": str(self.config.pseudos),
                }
                for previous in self.store.tasks():
                    if (
                        previous["kind"] == "prepare"
                        and previous["status"] == "succeeded"
                    ):
                        shared = self.config.artifacts / previous["id"]
                        if (shared / "tfa.mol").exists() and (
                            shared / "tfa.in"
                        ).exists():
                            try:
                                manifest = json.loads(
                                    (shared / "manifest.json").read_text()
                                )
                                if digest(shared / "tfa.in") == manifest["inputs"][
                                    "tfa.in"
                                ] and all(
                                    digest(self.config.pseudos / n) == h
                                    for n, h in manifest["pseudopotentials"].items()
                                ):
                                    request["shared_tfa"] = str(shared)
                                    break
                            except (OSError, KeyError, ValueError):
                                logger.warning(
                                    "Ignoring unavailable shared TFA from attempt %s",
                                    previous["id"],
                                )
                (directory / "request.json").write_text(json.dumps(request))
                command = [
                    self.config.prepare_python,
                    str(Path(__file__).with_name("preparation.py")),
                    str(directory / "request.json"),
                    str(directory),
                ]
                timeout = task["timeout"] or self.config.prepare_timeout
            else:
                command = task["command"]
            runtime = task.get("runtime", "native")
            if runtime == "apptainer":
                if task["kind"] not in ("qe", "estimate_ram"):
                    command[0] = "/opt/venv/bin/python"
                command = self.runtimes.wrap(
                    command, directory, task["resources"], task["image_hash"]
                )
            entrypoint = directory / "entrypoint.log"
            entrypoint.write_text(
                f"[{now()}] Task: {id}\nKind: {task['kind']}\nRuntime: {runtime}\n"
                f"Working directory: {directory}\n"
                f"Entrypoint: {shlex.join(command)}\n"
                f"Processes: {task['processes']}\nTimeout: {timeout}\n"
                f"Image hash: {task.get('image_hash')}\n"
                f"Source hash: {task.get('source_hash')}\n"
                f"Input hash: {task.get('input_hash')}\n"
                f"Resources: {json.dumps(task.get('resources', {}))}\n"
            )
            artifacts = {
                **task["artifacts"],
                "entrypoint.log": self.store.register(
                    id + "-entrypoint.log", entrypoint
                ),
            }
            self.store.update(id, command=command, artifacts=artifacts)

            def usage(value):
                self.store.update(id, usage=value)

            logger.info(
                "Task %s entrypoint: %s (cwd=%s)", id, shlex.join(command), directory
            )
            code, status, error = execute(
                command,
                directory,
                cancel,
                timeout,
                env=self.chem_env
                if task["kind"] not in ("qe", "estimate_ram")
                else None,
                on_usage=usage,
                container=runtime == "apptainer",
            )
            if status == "failed" and not error and task["kind"] != "estimate_ram":
                detail = tail(directory / "stderr.log", limit=2000).strip()
                error = f"Process exited with code {code}" + (
                    f": {detail}" if detail else ""
                )
            values = {
                "status": "interrupted" if self.stop.is_set() else status,
                "exit_code": code,
                "error": error,
                "ended": now(),
            }
            if task["kind"] == "estimate_ram":
                import itertools

                with (
                    (directory / "stdout.log").open(errors="replace") as stdout,
                    (directory / "stderr.log").open(errors="replace") as stderr,
                ):
                    report, qe_error = ram_reports(itertools.chain(stdout, stderr))
                values["ram_estimate"] = report
                if not error and status != "canceled" and code in (0, 255):
                    if qe_error:
                        values.update(
                            status="failed",
                            error="QE reported an error during RAM estimation",
                        )
                    elif report["per_process"] is None:
                        values.update(
                            status="failed",
                            error="QE did not report a per-process RAM estimate",
                        )
                    else:
                        values.update(status="succeeded", error="")
                if values["status"] == "failed" and not values["error"]:
                    values["error"] = f"Process exited with code {code}"
                if self.stop.is_set():
                    values["status"] = "interrupted"
            if task["kind"] == "qe":
                # Retain only scientific markers, bounding memory for long QE output.
                markers = []
                with (directory / "stdout.log").open(errors="replace") as stream:
                    for line in stream:
                        if any(
                            m in line
                            for m in (
                                "total energy",
                                "JOB DONE.",
                                "convergence",
                                "BFGS Geometry",
                                "bfgs converged",
                                "Error in routine",
                            )
                        ):
                            if "total energy" in line:
                                markers = [
                                    m for m in markers if "total energy" not in m
                                ]
                            markers.append(line)
                text = "".join(markers)
                calculation = re.search(
                    r"calculation\s*=\s*['\"]([^'\"]+)",
                    (directory / "input.in").read_text(),
                    re.IGNORECASE,
                )
                values["evidence"] = evidence(
                    text, calculation[1] if calculation else "scf"
                )
            self.store.update(id, **values)
        except Exception as error:
            logger.exception("Task %s could not execute", id)
            self.store.update(id, status="failed", error=str(error), ended=now())
        finally:
            artifacts = {}
            for name in (
                "entrypoint.log",
                "stdout.log",
                "stderr.log",
                "diagram.png",
                "candidate.in",
                "complex.in",
                "tfa.in",
                "input.in",
                "manifest.json",
            ):
                path = directory / name
                if path.is_file():
                    artifacts[name] = self.store.register(id + "-" + name, path)
            result = self.store.update(id, artifacts=artifacts)
            logger.info(
                "Task %s finished: %s (exit=%s)",
                id,
                result["status"],
                result.get("exit_code"),
            )
            if result.get("error"):
                logger.error("Task %s: %s", id, result["error"])

    def data(self):
        from .geometry import prepared_geometries

        tasks = self.store.tasks()
        for task in tasks:

            def registered_input(name, task_id=task["id"]):
                path = self.store.artifact(task_id + "-" + name)
                return Path(path) if path else None

            task["geometries"] = prepared_geometries(task, registered_input)
            if task["started"]:
                from datetime import datetime

                task["elapsed_seconds"] = (
                    datetime.fromisoformat(task["ended"] or now())
                    - datetime.fromisoformat(task["started"])
                ).total_seconds()
            directory = self.config.artifacts / task["id"]
            task["stdout_tail"] = tail(directory / "stdout.log")
            task["stderr_tail"] = tail(directory / "stderr.log")
        return {
            "mode": "live",
            "queue": {
                "settings": self.settings,
                "runtimes": self.runtimes.availability(),
            },
            "candidates": [c for c in self.candidates.values() if c["id"] != "tfa"],
            "reference": self.candidates["tfa"],
            "tasks": tasks,
            "input_versions": self.versions(),
        }
