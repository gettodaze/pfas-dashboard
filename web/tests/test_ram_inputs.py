"""Input provenance and initialization-only execution regressions."""

import hashlib
import sys
import threading

import pytest
from fastapi.testclient import TestClient

from dashboard.candidates import TFA
from dashboard.config import Config
from dashboard.export import export
from dashboard.inputs import discover, dry_input, ram_reports, validate_references
from dashboard.server import create_app
from dashboard.tasks import Manager

INPUT = """&CONTROL
 calculation='scf', pseudo_dir='./Pseudopotentials', outdir='./Outputs'
/
&SYSTEM
 nat=1, ntyp=1, ibrav=1, celldm(1)=12, ecutwfc=15
/
&ELECTRONS
/
ATOMIC_SPECIES
H 1.0 H.UPF
ATOMIC_POSITIONS angstrom
H 0 0 0
K_POINTS gamma
"""
REPORT = "Estimated max dynamical RAM per process > 12.50 MB\nEstimated total dynamical RAM > 1.25 GB\n"


@pytest.fixture
def manager(tmp_path):
    config = Config(
        artifacts=tmp_path / "artifacts",
        qe_inputs=tmp_path / "inputs",
        pseudos=tmp_path / "pseudos",
        pw=sys.executable,
        mpi=sys.executable,
    )
    config.pseudos.mkdir()
    (config.pseudos / "H.UPF").write_text("pseudo")
    return Manager(
        config,
        {
            "500": {"id": "500", "cid": "1", "smiles": "[H]", "fields": {}},
            "501": {"id": "501", "cid": "2", "smiles": "[H]", "fields": {}},
            "tfa": TFA,
        },
    )


def source(manager, owner="500", name="candidate.large-cell.in"):
    directory = manager.config.qe_inputs / owner
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(INPUT)
    return path


def review(manager, **kwargs):
    identifier = manager.versions()[0]["input_id"]
    return manager.preview(
        "500",
        "candidate",
        1,
        "local",
        input_id=identifier,
        kind="estimate_ram",
        **kwargs,
    )


def submit(manager, preview, **kwargs):
    return manager.queue(
        "estimate_ram",
        "500",
        input_id=preview["input_id"],
        expected_hash=preview["input_hash"],
        preview_id=preview["preview_id"],
        **kwargs,
    )


def test_discovery_separation_containment_refresh(manager, tmp_path):
    assert manager.versions() == []
    source(manager)
    source(manager, name="complex.close-contact.in")
    source(manager, "501")
    source(manager, "tfa", "tfa.small.in")
    source(manager, "500", "tfa.wrong.in")
    source(manager, "tfa", "candidate.wrong.in")
    source(manager, "500", "candidate.in")
    outside = tmp_path / "external.in"
    outside.write_text(INPUT)
    (manager.config.qe_inputs / "500" / "candidate.escape.in").symlink_to(outside)
    assert len(manager.versions()) == 4
    entries = discover(manager.config.qe_inputs, manager.candidates)
    identifier = next(i for i, (o, s, _) in entries.items() if o == "501")
    with pytest.raises(ValueError, match="identifier"):
        manager.preview("500", "candidate", 1, "local", input_id=identifier)
    identifier = next(
        i for i, (o, s, _) in entries.items() if o == "500" and s == "complex"
    )
    with pytest.raises(ValueError):
        manager.preview("500", "candidate", 1, "local", input_id=identifier)
    for invalid in ["../../external.in", str(outside), "bad"]:
        with pytest.raises(ValueError, match="identifier"):
            manager.preview("500", "candidate", 1, "local", input_id=invalid)
    tfa_id = next(i for i, (o, _, _) in entries.items() if o == "tfa")
    assert (
        manager.preview("501", "tfa", 1, "local", input_id=tfa_id)["version"]
        == "tfa.small.in"
    )
    source(manager, name="candidate.new.in")
    assert len(manager.data()["input_versions"]) == 5


@pytest.mark.parametrize("kind", ["qe", "estimate_ram"])
def test_snapshots_and_exports_without_preparation(manager, tmp_path, kind):
    path = source(manager)
    version = manager.versions()[0]["input_id"]
    preview = manager.preview(
        "500", "candidate", 1, "local", input_id=version, kind=kind
    )
    task = manager.queue(
        kind,
        "500",
        input_id=version,
        expected_hash=preview["input_hash"],
        preview_id=preview["preview_id"],
    )
    path.unlink()
    (manager.config.pseudos / "H.UPF").unlink()
    directory = manager.config.artifacts / task["id"]
    assert (directory / "input.in").read_text() == preview["input"]
    assert (directory / "Pseudopotentials/H.UPF").read_text() == "pseudo"
    assert task["source_hash"] == hashlib.sha256(INPUT.encode()).hexdigest()
    assert (
        task["executed_input_hash"]
        == hashlib.sha256(preview["input"].encode()).hexdigest()
    )
    assert task["timeout"] == (120 if kind == "estimate_ram" else None)
    assert preview["geometry"]["atoms"][0]["element"] == "H"
    exported = export(manager.config, tmp_path / "snapshot")["tasks"][0]
    assert exported["version"] == path.name
    assert exported["pseudopotentials"] == task["pseudopotentials"]
    assert not {"input_id", "input", "command"} & exported.keys()


@pytest.mark.parametrize("change", ["source", "pseudo", "processes", "kind", "deleted"])
def test_review_changes_rejected(manager, change):
    path = source(manager)
    preview = review(manager)
    if change == "source":
        path.write_text(INPUT.replace("calculation='scf'", "calculation='relax'"))
    elif change == "pseudo":
        (manager.config.pseudos / "H.UPF").write_text("new pseudo")
    elif change == "deleted":
        path.unlink()
    with pytest.raises(ValueError):
        if change == "kind":
            manager.queue(
                "qe",
                "500",
                input_id=preview["input_id"],
                expected_hash=preview["input_hash"],
                preview_id=preview["preview_id"],
            )
        else:
            submit(manager, preview, processes=2 if change == "processes" else 1)
    assert manager.store.tasks() == []


def test_requires_fresh_review(manager):
    path = source(manager)
    preview = review(manager)
    with pytest.raises(ValueError, match="preview"):
        manager.queue(
            "estimate_ram",
            "500",
            input_id=preview["input_id"],
            expected_hash=preview["input_hash"],
        )
    task = submit(manager, preview)
    assert path.read_text() == INPUT
    manager.cancel_task(task["id"])
    with pytest.raises(ValueError, match="preview"):
        submit(manager, preview)
    assert submit(manager, review(manager))["id"] != task["id"]


@pytest.mark.parametrize(
    "body",
    [
        "! nstep=8 /\n title='slash / and ! nstep=8',",
        "NSTEP = 45,",
        "nstep=2, nstep=3,",
        "title='doubled '' quote /', nstep = -2,",
    ],
)
def test_dry_transform(body):
    text = "&CoNtRoL\n" + body + "\n/\n&SYSTEM\n/\n"
    transformed = dry_input(text)
    assert "nstep=0" in transformed
    assert transformed.endswith("/\n&SYSTEM\n/\n")
    if "title=" in body:
        assert body.split("title=")[1].split(",")[0] in transformed
    assert dry_input(transformed) == transformed


@pytest.mark.parametrize(
    "text",
    [
        "&SYSTEM\n/",
        "&CONTROL nstep=2",
        "&CONTROL\n&SYSTEM\n/",
        "&CONTROL / &CONTROL /",
        "&CONTROL title='bad /",
        "&CONTROL nstep=1.5 /",
        "&CONTROL nstep='1' /",
        "&CONTROL nstep= /",
    ],
)
def test_malformed_control(text):
    with pytest.raises(ValueError):
        dry_input(text)


@pytest.mark.parametrize(
    "bad",
    [
        "! pseudo_dir='./Pseudopotentials'\n pseudo_dir='/tmp'",
        "pseudo_dir='./Pseudopotentials', pseudo_dir='/tmp'",
        "pseudo_dir='./Pseudopotentials', wfcdir='/tmp'",
        "pseudo_dir='./Pseudopotentials', prefix='../bad'",
        "pseudo_dir='./Pseudopotentials', wfcdir=42",
    ],
)
def test_reference_validation(bad):
    with pytest.raises(ValueError):
        validate_references("&CONTROL\n" + bad + ", outdir='./Outputs'\n/")


def test_missing_pseudo(manager):
    source(manager)
    (manager.config.pseudos / "H.UPF").unlink()
    with pytest.raises(ValueError, match="Missing pseudopotentials"):
        review(manager)


def test_units_and_missing_total():
    values, error = ram_reports(REPORT.splitlines())
    assert not error
    assert values["per_process"] == {
        "value": "12.50",
        "unit": "MB",
        "bytes": int(12.5 * 1024**2),
    }
    assert values["total"]["bytes"] == int(1.25 * 1024**3)
    values, error = ram_reports([REPORT.splitlines()[0]])
    assert values["total"] is None


@pytest.mark.parametrize(
    "code,status,error,output,expected,fragment",
    [
        (0, "succeeded", "", REPORT, "succeeded", ""),
        (255, "failed", "", REPORT, "succeeded", ""),
        (255, "failed", "", REPORT.splitlines()[0], "succeeded", ""),
        (0, "succeeded", "", REPORT + "Error in routine foo", "failed", "QE reported"),
        (255, "failed", "", REPORT + "%%%%%%", "failed", "QE reported"),
        (0, "succeeded", "", "JOB DONE.", "failed", "did not report"),
        (1, "failed", "", REPORT, "failed", "code 1"),
        (255, "failed", "Timed out", REPORT, "failed", "Timed out"),
        (0, "canceled", "Stopped", REPORT, "canceled", "Stopped"),
    ],
)
def test_estimate_outcomes(
    manager, monkeypatch, code, status, error, output, expected, fragment
):
    source(manager)
    task = submit(manager, review(manager))

    def execute(command, directory, *args, **kwargs):
        (directory / "stdout.log").write_text(output + "\n" + "x" * 20000)
        return code, status, error

    monkeypatch.setattr("dashboard.tasks.execute", execute)
    manager.run(task, threading.Event())
    result = manager.store.get(task["id"])
    assert result["status"] == expected
    assert (
        export(manager.config, manager.config.artifacts / "export")["tasks"][0][
            "ram_estimate"
        ]
        == result["ram_estimate"]
    )
    assert fragment in result["error"]
    assert "evidence" not in result
    assert (
        result["ram_estimate"]["per_process"] is not None
        or "did not report" in result["error"]
    )


def test_api_versions(manager):
    source(manager)
    app = create_app(manager.config)
    client = TestClient(app)
    identifier = client.get("/api/data").json()["input_versions"][0]["input_id"]
    request = {
        "candidate": "500",
        "system": "candidate",
        "kind": "estimate_ram",
        "input_id": identifier,
    }
    preview = client.post("/api/preview", json=request)
    assert preview.status_code == 200
    assert client.post("/api/tasks", json=request).status_code == 400
    assert (
        client.post(
            "/api/tasks",
            json={
                **request,
                "expected_hash": preview.json()["input_hash"],
                "preview_id": preview.json()["preview_id"],
            },
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "row",
    [
        "H 1 H.UPF\nC 12 ../bad.UPF",
        "H 1 H.UPF\nC 12 bad.dat",
        "H 1 H.UPF\nC bad C.UPF",
        "H 1 H.UPF\nC 12",
    ],
)
def test_species_reference_validation(manager, row):
    path = source(manager)
    path.write_text(INPUT.replace("H 1.0 H.UPF", row))
    with pytest.raises(ValueError):
        review(manager)


def test_apptainer_estimate_path(manager, monkeypatch):
    source(manager)
    monkeypatch.setattr(manager.runtimes, "verify", lambda: "image-hash")
    monkeypatch.setattr(manager.runtimes, "image_identity", lambda: "image-hash")
    preview = review(manager, runtime="apptainer")
    task = submit(manager, preview, runtime="apptainer")

    def wrap(command, directory, resources, image_hash):
        assert command == ["pw.x", "-in", "input.in"]
        assert resources["timeout"] == 120
        assert image_hash == "image-hash"
        return ["container", *command]

    monkeypatch.setattr(manager.runtimes, "wrap", wrap)

    def execute(command, directory, *args, **kwargs):
        assert command[0] == "container"
        assert kwargs["container"]
        (directory / "stdout.log").write_text(REPORT)
        return 255, "failed", ""

    monkeypatch.setattr("dashboard.tasks.execute", execute)
    manager.run(task)
    assert manager.store.get(task["id"])["status"] == "succeeded"


def test_legacy_preview_rejects_pseudo_edits(manager):
    directory = manager.config.artifacts / "prepared"
    directory.mkdir()
    (directory / "candidate.in").write_text(INPUT)
    manager.store.put(
        {
            "id": "prepared",
            "kind": "prepare",
            "status": "succeeded",
            "candidate": "500",
            "system": "candidate",
            "created": "",
            "started": None,
            "ended": None,
            "artifacts": {},
        }
    )
    preview = manager.preview("500", "candidate", 1, "local")
    (manager.config.pseudos / "H.UPF").write_text("edited")
    with pytest.raises(ValueError, match="preview"):
        manager.queue("qe", "500", expected_hash=preview["input_hash"])
