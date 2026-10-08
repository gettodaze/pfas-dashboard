"""Read-only scientific snapshot, with byte-preserving, hash-indexed downloads."""

import json
import math
import shutil
import sqlite3
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import quote

from .candidates import TFA, load
from .geometry import geometry, prepared_geometries
from .inputs import discover
from .persistence import now
from .preparation import digest, pseudo_names

SCIENTIFIC_SUFFIXES = {".in", ".png", ".cif", ".mol", ".xyz"}
TASK_FIELDS = (
    "id",
    "kind",
    "candidate",
    "system",
    "processes",
    "status",
    "created",
    "started",
    "ended",
    "exit_code",
    "evidence",
    "elapsed_seconds",
    "runtime",
    "resources",
    "usage",
    "image_hash",
    "version",
    "source_hash",
    "executed_input_hash",
    "pseudopotentials",
    "ram_estimate",
)


def _json(path):
    return json.loads(path.read_text()) if path.is_file() else {}


def _component(value):
    value = str(value)
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("Unsafe snapshot identifier")
    return value


def export(config, output):
    output = Path(output).absolute()
    # Never replace local scientific sources, even when the destination is misconfigured.
    for source in (config.artifacts, config.pseudos, config.qe_inputs):
        if source.resolve().is_relative_to(output.resolve()):
            raise ValueError("Snapshot destination overlaps source storage")
    if output.is_symlink():
        raise ValueError("Snapshot destination must not be a symlink")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".snapshot-", dir=output.parent) as temporary:
        stage = Path(temporary) / "snapshot"
        stage.mkdir()
        data = _export(config, stage)
        # Build first: a failed export leaves the previous snapshot intact.
        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(stage, output)
    return data


def _export(config, output):
    database = config.artifacts / "tasks.sqlite"
    raw = []
    if database.exists():
        # Do not instantiate Manager: it recovers tasks and can start calculations.
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
            raw = [
                json.loads(row[0])
                for row in db.execute("SELECT data FROM tasks ORDER BY rowid DESC")
            ]
    index: dict[str, Any] = {"algorithm": "sha256", "files": {}, "pseudopotentials": {}}
    pseudo_sources = {}
    hashed = {}

    def hash_file(path):
        if path not in hashed:
            hashed[path] = digest(path)
        return hashed[path]

    # Index immutable copies by name, without reading scratch/output trees.
    for task in raw:
        directory = config.artifacts / _component(task["id"])
        for path in (directory / "pseudo-snapshots").glob("*"):
            if path.is_file():
                pseudo_sources.setdefault(path.name, []).append(path)

    def copy_file(source, relative, **metadata):
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        sha256 = digest(destination)
        if sha256 != hash_file(source):
            raise ValueError(f"File changed while exporting: {source.name}")
        url = "snapshot/" + quote(relative.as_posix(), safe="/")
        index["files"][url] = {
            **metadata,
            "name": source.name,
            "sha256": sha256,
            "bytes": destination.stat().st_size,
        }
        return url

    def dependencies(path, expected, directory=None):
        try:
            names = pseudo_names(path.read_text())
        except (ValueError, UnicodeError):
            return [], "Cannot read ATOMIC_SPECIES pseudopotential references"
        result = []
        for name in names:
            wanted = expected.get(name)
            candidates = []
            if directory is not None:
                candidates.extend(
                    directory / folder / name
                    for folder in ("pseudo-snapshots", "Pseudopotentials")
                )
            candidates.append(config.pseudos / name)
            candidates.extend(pseudo_sources.get(name, []))
            source = next(
                (
                    p
                    for p in candidates
                    if p.is_file() and (wanted is None or hash_file(p) == wanted)
                ),
                None,
            )
            entry = {"name": name, "sha256": wanted, "status": "missing"}
            if source is not None:
                sha256 = hash_file(source)
                if sha256 not in index["pseudopotentials"]:
                    url = copy_file(
                        source,
                        Path("pseudopotentials") / f"{sha256}.UPF",
                        role="pseudopotential",
                    )
                    index["pseudopotentials"][sha256] = {"url": url, "names": []}
                shared = index["pseudopotentials"][sha256]
                if name not in shared["names"]:
                    shared["names"].append(name)
                entry.update(sha256=sha256, status="available", url=shared["url"])
            result.append(entry)
        return result, None

    tasks = []
    for task in raw:
        owner, task_id = _component(task["candidate"]), _component(task["id"])
        directory = config.artifacts / task_id
        files = [
            path
            for path in sorted(directory.glob("*"))
            if path.is_file() and path.suffix.lower() in SCIENTIFIC_SUFFIXES
        ]
        if not files:
            continue
        item = {k: task[k] for k in TASK_FIELDS if k in task}
        item["geometries"] = prepared_geometries(
            task, lambda name, directory=directory: directory / name
        )
        item["artifacts"] = {}
        item["input_assets"] = {}
        manifest = _json(directory / "manifest.json")
        # Scientific products live at the task root. Do not traverse Outputs/QE scratch.
        for path in files:
            role = "preparation" if task["kind"] == "prepare" else "executed"
            url = copy_file(
                path,
                Path("candidates") / owner / "tasks" / task_id / path.name,
                candidate=owner,
                task=task_id,
                role=role,
            )
            item["artifacts"][path.name] = url
            if path.suffix.lower() == ".in":
                expected = (
                    manifest.get("pseudopotentials", {})
                    if task["kind"] == "prepare"
                    else task.get("pseudopotentials", {})
                )
                refs, error = dependencies(path, expected, directory)
                entry = index["files"][url]
                entry["pseudopotentials"] = refs
                expected_hash = (
                    manifest.get("inputs", {}).get(path.name)
                    if task["kind"] == "prepare"
                    else task.get("executed_input_hash")
                    if path.name == "input.in"
                    else None
                )
                if expected_hash and entry["sha256"] != expected_hash:
                    raise ValueError(
                        f"Input no longer matches recorded hash: {task_id}/{path.name}"
                    )
                if error:
                    entry["dependency_error"] = error
                item["input_assets"][path.name] = {"url": url, **entry}
        tasks.append(item)

    candidates: list[dict[str, Any]] = load()
    # Save the displayed RAM values independently of exported task history.
    ram = {}
    for task in sorted(raw, key=lambda t: t.get("created", ""), reverse=True):
        value = (task.get("ram_estimate") or {}).get("per_process")
        if (
            task["kind"] == "estimate_ram"
            and task["status"] == "succeeded"
            and task.get("processes") == 1
            and value
            and task.get("system") in {"candidate", "complex", "tfa"}
        ):
            size = value.get("bytes")
            if isinstance(size, (int, float)) and math.isfinite(size) and size >= 0:
                ram.setdefault((task["candidate"], task["system"]), size / 1024**3)
    reference: dict[str, Any] = deepcopy(TFA)
    for candidate in [*candidates, reference]:
        for system in ("candidate", "complex", "tfa"):
            value = ram.get((candidate["id"], system))
            if system != "tfa" or candidate["id"] == "tfa":
                candidate["fields"][f"{system}_ram_per_process_gib"] = (
                    str(value) if value is not None else ""
                )
    versions = []
    for identifier, (owner, system, path) in discover(
        config.qe_inputs, [c["id"] for c in [*candidates, TFA]]
    ).items():
        url = copy_file(
            path,
            Path("candidates") / owner / "versions" / path.name,
            candidate=owner,
            system=system,
            role="version",
        )
        refs, error = dependencies(path, {})
        entry = index["files"][url]
        entry["pseudopotentials"] = refs
        if error:
            entry["dependency_error"] = error
        try:
            initial = geometry(path.read_text())
        except (ValueError, IndexError, UnicodeError) as error:
            initial = {"error": str(error)}
        versions.append(
            {
                "input_id": identifier,
                "candidate": owner,
                "system": system,
                "label": path.name,
                "artifacts": {path.name: url},
                "input_assets": {path.name: {"url": url, **entry}},
                "geometry": initial,
            }
        )
    data = {
        "mode": "snapshot",
        "timestamp": now(),
        "candidates": candidates,
        "reference": reference,
        "tasks": tasks,
        "input_versions": versions,
        "asset_index": "snapshot/asset-index.json",
    }
    (output / "asset-index.json").write_text(json.dumps(index, indent=2) + "\n")
    # Geometry for every candidate is substantial; compact the browser payload.
    (output / "data.json").write_text(json.dumps(data, separators=(",", ":")) + "\n")
    return data
