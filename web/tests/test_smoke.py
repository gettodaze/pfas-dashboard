"""Opt-in, bounded checks against installed chemistry and native QE tools."""

import os
import time

import pytest

from dashboard.candidates import TFA
from dashboard.config import Config
from dashboard.persistence import now
from dashboard.tasks import Manager

pytestmark = [
    pytest.mark.slow,
    pytest.mark.timeout(180),
    pytest.mark.skipif(
        os.getenv("PFAS_SMOKE") != "1",
        reason="Set PFAS_SMOKE=1 and PFAS_CHEM_PYTHON for native checks",
    ),
]


def finished(manager, id, limit):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        task = manager.store.get(id)
        if task["status"] not in ("queued", "running"):
            return task
        time.sleep(0.1)
    manager.cancel_task(id)
    raise AssertionError("Smoke task exceeded its bound")


def test_real_preparation_and_diagram(tmp_path):
    config = Config(artifacts=tmp_path, prepare_timeout=90)
    manager = Manager(
        config,
        {"500": {"id": "500", "smiles": "O", "cid": "962", "fields": {}}, "tfa": TFA},
    )
    try:
        diagram = manager.queue("diagram", "500")
        preparation = manager.queue("prepare", "500")
        manager.start()
        assert finished(manager, diagram["id"], 65)["status"] == "succeeded"
        task = finished(manager, preparation["id"], 95)
        assert task["status"] == "succeeded", manager.data()["tasks"]
        assert all(
            (tmp_path / task["id"] / name).exists()
            for name in ["tfa.in", "candidate.in", "complex.in", "manifest.json"]
        )
    finally:
        manager.close()


@pytest.mark.parametrize("processes", [1, 2])
def test_native_qe(tmp_path, processes):
    config = Config(artifacts=tmp_path)
    manager = Manager(
        config,
        {
            "500": {"id": "500", "smiles": "[H][H]", "cid": "783", "fields": {}},
            "tfa": TFA,
        },
    )
    directory = tmp_path / "prepared"
    directory.mkdir()
    (directory / "candidate.in").write_text("""&CONTROL
 calculation='scf', prefix='h2', pseudo_dir='./Pseudopotentials', outdir='./Outputs'
/
&SYSTEM
 ibrav=1, celldm(1)=12, nat=2, ntyp=1, ecutwfc=15, ecutrho=120
/
&ELECTRONS
 conv_thr=1d-6
/
ATOMIC_SPECIES
H 1.00794 H.UPF
ATOMIC_POSITIONS angstrom
H 3 3 3
H 3 3 3.74
K_POINTS gamma
""")
    manager.store.put(
        {
            "id": "prepared",
            "kind": "prepare",
            "candidate": "500",
            "system": "candidate",
            "status": "succeeded",
            "artifacts": {},
            "created": now(),
            "started": None,
            "ended": None,
        }
    )
    preview = manager.preview("500", "candidate", processes, "local")
    task = manager.queue(
        "qe",
        "500",
        processes=processes,
        timeout=45,
        expected_hash=preview["input_hash"],
    )
    manager.start()
    try:
        result = finished(manager, task["id"], 50)
        assert result["status"] == "succeeded", manager.data()["tasks"]
        assert result["evidence"]["confirmed"], result
    finally:
        manager.close()
