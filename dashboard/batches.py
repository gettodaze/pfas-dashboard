"""Batch review and submission; batches never imply calculation dependencies."""

import json
import logging
import math
import uuid

from .persistence import now
from .runtimes import GIB

logger = logging.getLogger("uvicorn.error")


class Batches:
    def __init__(self, manager):
        self.manager = manager

    def review(
        self,
        candidates,
        kind,
        system="candidate",
        runtime="native",
        processes=1,
        memory_gib=4,
        timeout=None,
        retry=False,
        rerun_completed=False,
        target="local",
        issue_tokens=True,
        **_,
    ):
        manager = self.manager
        if target != "local":
            raise NotImplementedError("Slurm execution is not implemented")
        if kind not in ("diagram", "prepare", "qe", "estimate_ram") or system not in (
            "candidate",
            "complex",
            "tfa",
        ):
            raise ValueError("Unknown batch action")
        if (
            not candidates
            or len(candidates) > 1001
            or len(set(candidates)) != len(candidates)
        ):
            raise ValueError("Provide distinct selected candidate IDs")
        if any(id not in manager.candidates for id in candidates):
            raise ValueError("Unknown candidate")
        logger.info(
            "Batch preview: reviewing %s selected %s jobs", len(candidates), kind
        )
        timeout = (
            timeout
            if timeout is not None
            else manager.config.prepare_timeout
            if kind == "prepare"
            else 60
            if kind == "diagram"
            else 120
            if kind == "estimate_ram"
            else None
        )
        resources = manager.resources(
            runtime,
            processes if kind in ("qe", "estimate_ram") else 1,
            memory_gib,
            timeout,
        )
        identity = manager.runtimes.verify() if runtime == "apptainer" else None
        molecules = {}
        if kind == "prepare":
            # One bounded environment/SMILES probe, rather than hundreds of child probes.
            probe_code = """import shutil,json,sys
import rdkit,pymatgen.core,ase,openbabel
from rdkit import Chem
assert shutil.which('obabel') and shutil.which('cif2cell')
results={}
for id,smiles in json.load(sys.stdin).items():
    molecule=Chem.MolFromSmiles(smiles)
    results[id] = sorted({a.GetSymbol() for a in molecule.GetAtoms()} | {'H','C','O','F'}) if molecule is not None else None
print(json.dumps(results))
"""
            result = manager.runtimes.chemistry_probe(
                runtime,
                probe_code,
                input_text=json.dumps(
                    {id: manager.candidates[id]["smiles"] for id in candidates}
                ),
            )
            if result.returncode:
                raise ValueError(
                    "Preparation tools unavailable: " + result.stderr[-1000:]
                )
            molecules = json.loads(getattr(result, "stdout", "{}"))
        tasks = manager.store.tasks()
        related_tasks = {}
        prepared_tasks = {}
        all_prepared = []
        for task in tasks:
            related_tasks.setdefault(
                (task["candidate"], task["kind"], task["system"]), []
            ).append(task)
            if task["kind"] == "prepare" and task["status"] == "succeeded":
                prepared_tasks.setdefault(task["candidate"], []).append(task)
                all_prepared.append(task)
        # Share only within this review. Submission rereads history and file hashes.
        pseudo_hashes = {}
        entries = []
        for candidate in candidates:
            entry = {"candidate": candidate, "status": "eligible", "reason": ""}
            owner_system = (
                system
                if kind in ("qe", "estimate_ram")
                else "tfa"
                if candidate == "tfa"
                else "candidate"
            )
            if candidate in molecules:
                elements = molecules[candidate]
                missing = [
                    element + ".UPF"
                    for element in elements or []
                    if not (manager.config.pseudos / (element + ".UPF")).is_file()
                ]
                if elements is None:
                    entry.update(
                        status="unavailable", reason="Invalid representative SMILES"
                    )
                elif missing:
                    entry.update(
                        status="unavailable",
                        reason="Missing pseudopotentials: " + ", ".join(missing),
                    )
            related = related_tasks.get((candidate, kind, owner_system), [])
            if entry["status"] == "unavailable":
                pass
            elif any(t["status"] in ("queued", "running") for t in related):
                entry.update(status="active", reason="Already queued or running")
            else:
                try:
                    if kind in ("qe", "estimate_ram"):
                        preview = manager.preview(
                            candidate,
                            system,
                            processes,
                            "local",
                            runtime,
                            kind=kind,
                            issue_token=issue_tokens,
                            _tasks=all_prepared
                            if system == "tfa"
                            else prepared_tasks.get(candidate, []),
                            _pseudo_hashes=pseudo_hashes,
                        )
                        entry.update(preview)
                        if not (
                            retry or (kind == "estimate_ram" and rerun_completed)
                        ) and any(
                            t["status"] == "succeeded"
                            and t.get("input_hash") == preview["input_hash"]
                            and t.get("pseudopotentials") == preview["pseudopotentials"]
                            and (
                                t.get("evidence", {}).get("confirmed")
                                if kind == "qe"
                                else t.get("ram_estimate", {}).get("per_process")
                                and t.get("source_hash") == preview["source_hash"]
                                and t.get("runtime", "native") == runtime
                                and t.get("processes") == processes
                                and t.get("image_hash") == identity
                            )
                            for t in related
                        ):
                            entry.update(
                                status="completed",
                                reason="Matching confirmed result exists"
                                if kind == "qe"
                                else "Matching QE RAM estimate exists",
                            )
                    elif not retry:
                        names = (
                            ["diagram.png"]
                            if kind == "diagram"
                            else ["tfa.in"]
                            if candidate == "tfa"
                            else [
                                "candidate.in",
                                "complex.in",
                                "tfa.in",
                                "manifest.json",
                            ]
                        )
                        if any(
                            t["status"] == "succeeded"
                            and all(
                                (manager.config.artifacts / t["id"] / name).is_file()
                                for name in names
                            )
                            for t in related
                        ):
                            entry.update(
                                status="completed",
                                reason="Existing artifacts are available",
                            )
                except (ValueError, OSError) as error:
                    entry.update(status="unavailable", reason=str(error))
            entries.append(entry)
            if len(entries) % 100 == 0 or len(entries) == len(candidates):
                logger.info(
                    "Batch preview: reviewed %s/%s jobs", len(entries), len(candidates)
                )
        return {
            "entries": entries,
            "resources": resources,
            "image_hash": identity,
            "runtime": runtime,
        }

    def submit(self, request):
        manager = self.manager
        batch_id = request.get("id") or uuid.uuid4().hex
        if not isinstance(batch_id, str) or not 1 <= len(batch_id) <= 100:
            raise ValueError("Invalid batch ID")
        with manager.guard:
            previous = manager.store.batch(batch_id)
            if previous:
                return previous
            logger.info("Batch %s: validating selected jobs before queueing", batch_id)
            review = self.review(**request, issue_tokens=False)
            if review["image_hash"] != request.get("image_hash"):
                raise ValueError("Container image changed; review the batch again")
            expected = {e["candidate"]: e for e in request.get("expected", [])}
            batch = {
                "id": batch_id,
                "created": now(),
                "kind": request["kind"],
                "system": request.get("system", "candidate"),
                "runtime": review["runtime"],
                "resources": review["resources"],
                "image_hash": review["image_hash"],
                "rerun_completed": request.get("rerun_completed", False)
                if request["kind"] == "estimate_ram"
                else False,
                "candidates": request["candidates"],
                "entries": [],
                "task_ids": [],
            }
            # Persist first for idempotency. A restart retains partially accepted work.
            manager.store.put_batch(batch)
            for position, entry in enumerate(review["entries"]):
                if entry["status"] == "eligible":
                    old = expected.get(entry["candidate"])
                    if (
                        not old
                        or old.get("status") != "eligible"
                        or (
                            request["kind"] in ("qe", "estimate_ram")
                            and (
                                old.get("input_hash") != entry.get("input_hash")
                                or (
                                    request["kind"] == "estimate_ram"
                                    and old.get("source_hash")
                                    != entry.get("source_hash")
                                )
                                or old.get("pseudopotentials")
                                != entry.get("pseudopotentials")
                            )
                        )
                    ):
                        entry.update(
                            status="unavailable",
                            reason="Entry changed since preview; review again",
                        )
                    else:
                        try:
                            task = manager.queue(
                                request["kind"],
                                entry["candidate"],
                                system=request.get("system", "candidate"),
                                processes=request.get("processes", 1),
                                timeout=request.get("timeout"),
                                expected_hash=entry.get("input_hash"),
                                preview_id=old.get("preview_id")
                                if request["kind"] == "estimate_ram"
                                else None,
                                expected_pseudos=entry.get("pseudopotentials"),
                                runtime=review["runtime"],
                                memory_gib=request.get("memory_gib", 4),
                                batch_id=batch_id,
                                position=position,
                                checked_preflight=True,
                            )
                            batch["task_ids"].append(task["id"])
                            entry = {
                                "candidate": entry["candidate"],
                                "status": "queued",
                                "task_id": task["id"],
                                "reason": "",
                            }
                        except Exception as error:
                            logging.getLogger("uvicorn.error").exception(
                                "Batch %s: cluster %s could not be queued",
                                batch_id,
                                entry["candidate"],
                            )
                            entry = {
                                "candidate": entry["candidate"],
                                "status": "unavailable",
                                "reason": str(error),
                            }
                # Raw QE inputs belong to job snapshots, not batch metadata.
                batch["entries"].append(
                    {
                        k: v
                        for k, v in entry.items()
                        if k in ("candidate", "status", "task_id", "reason")
                    }
                )
                manager.store.put_batch(batch)
                if (position + 1) % 100 == 0 or position + 1 == len(review["entries"]):
                    logger.info(
                        "Batch %s: accepted %s/%s entries (%s queued)",
                        batch_id,
                        position + 1,
                        len(review["entries"]),
                        len(batch["task_ids"]),
                    )
            return batch

    def retry(self, id):
        batch = self.manager.store.batch(id)
        if not batch:
            raise ValueError("Unknown batch")
        tasks = [self.manager.store.get(t) for t in batch["task_ids"]]
        candidates = [
            t["candidate"]
            for t in tasks
            if t["status"] in ("failed", "canceled", "interrupted")
            or (
                t["kind"] == "qe"
                and t["status"] == "succeeded"
                and not t.get("evidence", {}).get("confirmed")
            )
        ]
        candidates += [
            e["candidate"]
            for e in batch["entries"]
            if e["status"] == "unavailable" and e["candidate"] not in candidates
        ]
        candidates = [id for id in batch["candidates"] if id in set(candidates)]
        resources = batch["resources"]
        return {
            "candidates": candidates,
            "kind": batch["kind"],
            "system": batch["system"],
            "runtime": batch["runtime"],
            "memory_gib": resources["memory_bytes"] / GIB,
            "processes": resources["cpus"],
            "timeout": resources["timeout"],
            "retry": True,
        }


def validate_positive(value, label, integer=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or value <= 0
        or (integer and int(value) != value)
    ):
        raise ValueError(
            f"{label} must be a positive {'integer' if integer else 'number'}"
        )
