"""Opt-in checks on a built Apptainer image; never install/build it implicitly."""

import json
import os
import shutil
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
        os.getenv("PFAS_CONTAINER_SMOKE") != "1"
        or not os.getenv("PFAS_APPTAINER_IMAGE")
        or not shutil.which(os.getenv("PFAS_APPTAINER", "apptainer")),
        reason="Set PFAS_CONTAINER_SMOKE=1 and configure an installed Apptainer image",
    ),
]


def wait(manager, id, limit=100):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        task = manager.store.get(id)
        if task["status"] not in ("queued", "running"):
            return task
        time.sleep(0.1)
    manager.cancel_task(id)
    raise AssertionError("Container smoke exceeded its bound")


def manager_for(tmp_path):
    return Manager(
        Config(artifacts=tmp_path, prepare_timeout=90),
        {
            "500": {"id": "500", "cid": "962", "smiles": "O", "fields": {}},
            "501": {"id": "501", "cid": "962", "smiles": "O", "fields": {}},
            "tfa": TFA,
        },
    )


def test_container_preparation(tmp_path):
    manager = manager_for(tmp_path)
    try:
        task = manager.queue("prepare", "500", runtime="apptainer", timeout=90)
        manager.start()
        result = wait(manager, task["id"])
        assert result["status"] == "succeeded", result
        assert all(
            (tmp_path / task["id"] / name).is_file()
            for name in ("candidate.in", "complex.in", "tfa.in")
        )
    finally:
        manager.close()


def qe_source(manager):
    directory = manager.config.artifacts / "prepared"
    directory.mkdir()
    (directory / "candidate.in").write_text("""&CONTROL
calculation='scf', prefix='h2', pseudo_dir='./Pseudopotentials', outdir='./Outputs'
/
&SYSTEM
ibrav=1,celldm(1)=12,nat=2,ntyp=1,ecutwfc=15,ecutrho=120
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


@pytest.mark.parametrize("processes", [1, 2])
def test_container_qe(tmp_path, processes):
    manager = manager_for(tmp_path)
    try:
        qe_source(manager)
        preview = manager.preview("500", "candidate", processes, "local", "apptainer")
        task = manager.queue(
            "qe",
            "500",
            processes=processes,
            runtime="apptainer",
            timeout=45,
            expected_hash=preview["input_hash"],
        )
        manager.start()
        result = wait(manager, task["id"], 60)
        assert result["status"] == "succeeded", result
        assert result["evidence"]["confirmed"], result
    finally:
        manager.close()


def test_container_oom_cleanup_and_next_job(tmp_path):
    manager = manager_for(tmp_path)
    try:
        qe_source(manager)
        preview = manager.preview("500", "candidate", 1, "local", "apptainer")
        task = manager.queue(
            "qe",
            "500",
            runtime="apptainer",
            memory_gib=0.125,
            timeout=30,
            expected_hash=preview["input_hash"],
        )
        # Use the same supervised runtime, with an intentionally oversized child.
        code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']); subprocess.call([sys.executable,'-c','x=bytearray(256*1024*1024)']);time.sleep(120)"
        manager.store.update(task["id"], command=["/opt/venv/bin/python", "-c", code])
        next_job = manager.queue("diagram", "501", runtime="apptainer", memory_gib=0.25)
        manager.start()
        failed = wait(manager, task["id"], 40)
        assert failed["status"] == "failed" and "Memory limit" in failed["error"], (
            failed
        )
        assert wait(manager, next_job["id"], 65)["status"] == "succeeded"
        report = json.loads((tmp_path / task["id"] / "resource.json").read_text())
        from pathlib import Path

        assert not Path(report["cgroup"]).exists(), (
            "Container descendants/cgroup survived termination"
        )
    finally:
        manager.close()
