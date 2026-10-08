"""Exercise diagram generation with the documented default web environment."""

import time

import pytest

from dashboard.candidates import load
from dashboard.config import Config
from dashboard.tasks import Manager


@pytest.mark.timeout(5)
@pytest.mark.parametrize("valid", [True, False])
def test_default_diagram_environment(tmp_path, monkeypatch, valid):
    monkeypatch.delenv("PFAS_CHEM_PYTHON", raising=False)
    candidate = load()[0]
    if not valid:
        candidate = {**candidate, "smiles": "invalid-smiles"}
    manager = Manager(Config(artifacts=tmp_path), {candidate["id"]: candidate})
    task = manager.queue("diagram", candidate["id"])
    manager.start()
    try:
        deadline = time.monotonic() + 4
        while manager.store.get(task["id"])["status"] in ("queued", "running"):
            assert time.monotonic() < deadline, "Diagram exceeded test bound"
            time.sleep(0.01)
    finally:
        manager.close()
    result = manager.store.get(task["id"])
    if valid:
        assert result["status"] == "succeeded", result
        assert (
            (tmp_path / task["id"] / "diagram.png").read_bytes().startswith(b"\x89PNG")
        )
        assert "diagram.png" in result["artifacts"]
    else:
        assert result["status"] == "failed"
        assert "Invalid representative SMILES" in result["error"]
        assert "stderr.log" in result["artifacts"]
