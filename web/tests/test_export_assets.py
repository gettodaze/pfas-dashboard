"""Scientific downloads preserve bytes, provenance, and static URL scope."""

import hashlib
import json
import sqlite3
from urllib.parse import unquote, urljoin

import pytest

from dashboard.config import Config
from dashboard.export import export

INPUT = b"&SYSTEM\r\n nat=1, ntyp=1, ibrav=0\r\n/\r\nATOMIC_SPECIES\r\nH 1 H.UPF\r\nATOMIC_POSITIONS angstrom\r\nH 0 0 0\r\nK_POINTS gamma\r\n"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fixture(tmp_path):
    config = Config(
        artifacts=tmp_path / "local",
        pseudos=tmp_path / "pseudos",
        qe_inputs=tmp_path / "versions",
    )
    config.artifacts.mkdir()
    config.pseudos.mkdir()
    (config.pseudos / "H.UPF").write_bytes(b"new pseudo")
    tasks = []
    for identifier, kind, pseudo in (
        ("prepare", "prepare", b"old pseudo"),
        ("run", "qe", b"new pseudo"),
        ("duplicate", "qe", b"old pseudo"),
    ):
        directory = config.artifacts / identifier
        directory.mkdir()
        name = "candidate.in" if kind == "prepare" else "input.in"
        (directory / name).write_bytes(INPUT)
        (directory / "pseudo-snapshots").mkdir()
        (directory / "pseudo-snapshots/H.UPF").write_bytes(pseudo)
        (directory / "stdout.log").write_text("private log")
        (directory / "request.json").write_text('{"secret":"private"}')
        (directory / "Outputs").mkdir()
        (directory / "Outputs/scratch.in").write_bytes(INPUT)
        task = {
            "id": identifier,
            "candidate": "439",
            "kind": kind,
            "status": "succeeded",
            "system": "candidate",
            "created": identifier,
            "pseudopotentials": {"H.UPF": sha(pseudo)},
            "executed_input_hash": sha(INPUT),
        }
        if kind == "prepare":
            (directory / "manifest.json").write_text(
                json.dumps(
                    {
                        "inputs": {name: sha(INPUT)},
                        "pseudopotentials": task["pseudopotentials"],
                    }
                )
            )
            for extension in ("png", "cif", "mol", "xyz"):
                (directory / f"candidate.{extension}").write_bytes(extension.encode())
        tasks.append(task)
    tasks.extend(
        [
            {"id": "empty", "candidate": "438", "kind": "prepare", "status": "failed"},
            {
                "id": "ram",
                "candidate": "439",
                "kind": "estimate_ram",
                "status": "succeeded",
                "created": "2026-10-01",
                "system": "complex",
                "processes": 1,
                "ram_estimate": {"per_process": {"bytes": 2 * 1024**3}},
            },
            {
                "id": "failed-ram",
                "candidate": "439",
                "kind": "estimate_ram",
                "status": "failed",
                "created": "2026-10-02",
                "system": "complex",
                "processes": 1,
                "ram_estimate": {"per_process": {"bytes": 8 * 1024**3}},
            },
        ]
    )
    with sqlite3.connect(config.artifacts / "tasks.sqlite") as db:
        db.execute("create table tasks (data text)")
        db.executemany(
            "insert into tasks values (?)", [(json.dumps(t),) for t in tasks]
        )
    version = config.qe_inputs / "439/candidate.external-version.in"
    version.parent.mkdir(parents=True)
    version.write_bytes(INPUT)
    return config


def local(output, url):
    return output / unquote(url.removeprefix("snapshot/"))


def test_scientific_export_hashes_versions_ram_and_clean_rebuild(tmp_path):
    config = fixture(tmp_path)
    output = tmp_path / "snapshot"
    before = (config.artifacts / "tasks.sqlite").read_bytes()
    data = export(config, output)
    assert (config.artifacts / "tasks.sqlite").read_bytes() == before
    assert {t["id"] for t in data["tasks"]} == {"prepare", "run", "duplicate"}
    candidate = next(c for c in data["candidates"] if c["id"] == "439")
    assert candidate["fields"]["complex_ram_per_process_gib"] == "2.0"
    index = json.loads((output / "asset-index.json").read_text())
    assert len(index["pseudopotentials"]) == 2
    for url, metadata in index["files"].items():
        assert urljoin("https://example.org/pfas-dashboard/", url).startswith(
            "https://example.org/pfas-dashboard/snapshot/"
        )
        assert sha(local(output, url).read_bytes()) == metadata["sha256"]
    for task in data["tasks"]:
        for asset in task["input_assets"].values():
            assert local(output, asset["url"]).read_bytes() == INPUT
            pseudo = asset["pseudopotentials"][0]
            expected = task["pseudopotentials"]["H.UPF"]
            assert pseudo["sha256"] == expected
            assert sha(local(output, pseudo["url"]).read_bytes()) == expected
    assert data["input_versions"][0]["label"] == "candidate.external-version.in"
    assert (
        local(
            output, next(iter(data["input_versions"][0]["artifacts"].values()))
        ).read_bytes()
        == INPUT
    )
    assert not any(p.suffix in {".log", ".sqlite", ".sif"} for p in output.rglob("*"))
    assert not list(output.rglob("scratch.in"))
    (config.artifacts / "prepare/candidate.png").unlink()
    (config.qe_inputs / "439/candidate.external-version.in").unlink()
    export(config, output)
    assert not list(output.rglob("*.png"))
    assert not list(output.rglob("*external*"))


def test_missing_historical_pseudo_has_no_wrong_version_link(tmp_path):
    config = fixture(tmp_path)
    for task in ("prepare", "duplicate"):
        (config.artifacts / task / "pseudo-snapshots/H.UPF").unlink()
    data = export(config, tmp_path / "snapshot")
    task = next(t for t in data["tasks"] if t["id"] == "prepare")
    ref = task["input_assets"]["candidate.in"]["pseudopotentials"][0]
    assert ref == {"name": "H.UPF", "sha256": sha(b"old pseudo"), "status": "missing"}


def test_changed_input_aborts_without_replacing_snapshot(tmp_path):
    config = fixture(tmp_path)
    output = tmp_path / "snapshot"
    export(config, output)
    before = (output / "data.json").read_bytes()
    (config.artifacts / "run/input.in").write_bytes(INPUT + b"! edited\n")
    with pytest.raises(ValueError, match="recorded hash"):
        export(config, output)
    assert (output / "data.json").read_bytes() == before


def test_export_rejects_source_destination(tmp_path):
    config = fixture(tmp_path)
    with pytest.raises(ValueError, match="overlaps"):
        export(config, config.artifacts)
