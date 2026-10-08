"""Opt-in native QE initialization smoke: no SCF iterations."""

import os
import time

import pytest

from dashboard.candidates import TFA
from dashboard.config import Config
from dashboard.tasks import Manager


@pytest.mark.slow
@pytest.mark.timeout(150)
@pytest.mark.skipif(
    os.getenv("PFAS_RAM_SMOKE") != "1",
    reason="Set PFAS_RAM_SMOKE=1 with native QE and H.UPF",
)
def test_real_qe_estimate(tmp_path):
    config = Config(artifacts=tmp_path / "artifacts", qe_inputs=tmp_path / "inputs")
    manager = Manager(config, {"tfa": TFA})
    directory = config.qe_inputs / "tfa"
    directory.mkdir(parents=True)
    (directory / "tfa.hydrogen-smoke.in").write_text("""&CONTROL
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
    preview = manager.preview(
        "tfa",
        "tfa",
        1,
        "local",
        input_id=manager.versions()[0]["input_id"],
        kind="estimate_ram",
    )
    task = manager.queue(
        "estimate_ram",
        "tfa",
        system="tfa",
        input_id=preview["input_id"],
        expected_hash=preview["input_hash"],
        preview_id=preview["preview_id"],
    )
    manager.start()
    try:
        deadline = time.monotonic() + 125
        while time.monotonic() < deadline:
            result = manager.store.get(task["id"])
            if result["status"] not in ("queued", "running"):
                break
            time.sleep(0.1)
        assert result["status"] == "succeeded", result
        assert result["ram_estimate"]["per_process"]
        output = (config.artifacts / task["id"] / "stdout.log").read_text()
        assert "iteration #" not in output.lower()
        assert "!    total energy" not in output.lower()
        assert "evidence" not in result
    finally:
        manager.close()
