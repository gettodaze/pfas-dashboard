import json
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from dashboard.candidates import TFA
from dashboard.config import Config
from dashboard.persistence import now
from dashboard.runtimes import GIB
from dashboard.server import create_app
from dashboard.tasks import Manager

pytestmark = pytest.mark.timeout(8)


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr("dashboard.tasks.memory_ceiling", lambda: 16 * GIB)
    config = Config(artifacts=tmp_path / "artifacts", pseudos=tmp_path / "pseudos")
    config.pseudos.mkdir()
    (config.pseudos / "H.UPF").write_text("hydrogen")
    candidates = {
        id: {"id": id, "cid": id, "smiles": "O", "fields": {}}
        for id in ("500", "501", "502")
    }
    candidates["tfa"] = TFA
    m = Manager(config, candidates)
    yield m
    m.close()


def request(ids, kind="diagram", **kwargs):
    return {
        "candidates": ids,
        "kind": kind,
        "system": "candidate",
        "runtime": "native",
        "processes": 1,
        "memory_gib": 4,
        **kwargs,
    }


def submit(manager, body):
    review = manager.batches.review(**body)
    return manager.batches.submit(
        {**body, "expected": review["entries"], "image_hash": review["image_hash"]}
    )


def wait_tasks(manager, limit=5):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if all(t["status"] not in ("queued", "running") for t in manager.store.tasks()):
            return
        time.sleep(0.02)
    raise AssertionError(manager.store.tasks())


def prepare_qe(manager, candidate):
    id = "prepared-" + candidate
    directory = manager.config.artifacts / id
    directory.mkdir()
    (directory / "candidate.in").write_text(
        "&CONTROL\ncalculation='scf', pseudo_dir='./Pseudopotentials', outdir='./Outputs'\n/\nATOMIC_SPECIES\nH 1 H.UPF\n"
    )
    manager.store.put(
        {
            "id": id,
            "kind": "prepare",
            "candidate": candidate,
            "system": "candidate",
            "status": "succeeded",
            "artifacts": {},
            "created": now(),
            "started": None,
            "ended": None,
        }
    )
    return directory / "candidate.in"


def test_order_dedup_failure_continuation_and_retry(manager, tmp_path):
    executable = tmp_path / "diagram-python"
    executable.write_text(
        f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\nif sys.argv[2]=='bad':\n print('invalid molecule',file=sys.stderr);sys.exit(2)\nPath(sys.argv[3]).write_bytes(b'png')\n"
    )
    executable.chmod(0o755)
    manager.config.python = str(executable)
    manager.candidates["501"]["smiles"] = "bad"
    body = request(["501", "500", "502"], id="ordered")
    batch = submit(manager, body)
    assert [t["candidate"] for t in manager.store.queued()] == body["candidates"]
    assert submit(manager, body) == batch
    assert len(manager.store.tasks()) == 3
    assert (
        manager.batches.review(**request(["500"]))["entries"][0]["status"] != "eligible"
    )
    manager.start()
    wait_tasks(manager)
    statuses = {t["candidate"]: t["status"] for t in manager.store.tasks()}
    assert statuses == {"501": "failed", "500": "succeeded", "502": "succeeded"}
    failed = manager.store.get(batch["task_ids"][0])
    assert "invalid molecule" in failed["error"]
    assert failed["artifacts"]["stderr.log"]
    retry = manager.batches.retry(batch["id"])
    assert retry["candidates"] == ["501"]
    assert (
        manager.batches.review(**request(["500"]))["entries"][0]["status"]
        == "completed"
    )
    manager.configure({"paused": True})
    retried = submit(manager, retry)
    assert retried["task_ids"][0] != failed["id"]


def test_bulk_preparation_common_preflight_once(manager, monkeypatch):
    calls = []
    monkeypatch.setattr(
        manager.runtimes,
        "chemistry_probe",
        lambda *args, **kwargs: (
            calls.append(args) or SimpleNamespace(returncode=0, stderr="")
        ),
    )
    monkeypatch.setattr(
        manager, "preflight", lambda _: pytest.fail("Per-entry synchronous preflight")
    )
    batch = submit(manager, request(["500", "501"], "prepare"))
    assert len(batch["task_ids"]) == 2
    assert (
        len(calls) == 2
    )  # Preview and acceptance each validate the common environment.
    assert all(t["resources"]["timeout"] == 900 for t in manager.store.tasks())


def test_qe_review_skip_hash_changes_and_immutable_inputs(manager):
    manager.config.pw = sys.executable
    source = prepare_qe(manager, "500")
    body = request(["500", "501"], "qe")
    review = manager.batches.review(**body)
    assert [e["status"] for e in review["entries"]] == ["eligible", "unavailable"]
    batch = manager.batches.submit({**body, "expected": review["entries"]})
    task = manager.store.get(batch["task_ids"][0])
    manager.store.update(task["id"], status="succeeded", evidence={"confirmed": True})
    assert manager.batches.review(**body)["entries"][0]["status"] == "completed"
    source.write_text(source.read_text() + "! edited\n")
    assert (
        manager.config.artifacts / task["id"] / "input.in"
    ).read_text() != source.read_text()
    assert manager.batches.review(**body)["entries"][0]["status"] == "eligible"
    changed = manager.batches.submit({**body, "expected": review["entries"]})
    assert changed["task_ids"] == []
    assert "changed" in changed["entries"][0]["reason"]
    current = manager.batches.review(**body)
    (manager.config.pseudos / "H.UPF").write_text("changed pseudo")
    assert (
        manager.batches.submit({**body, "expected": current["entries"]})["task_ids"]
        == []
    )


@pytest.mark.parametrize("runtime,maximum", [("native", 1), ("apptainer", 2)])
def test_scheduler_reservations_and_native_serial(
    manager, monkeypatch, runtime, maximum
):
    monkeypatch.setattr(manager.runtimes, "verify", lambda: "image")
    manager.configure(
        {"paused": True, "concurrency": 3, "memory_bytes": 8 * GIB, "cpus": 2}
    )
    running, observed, order = set(), [], []
    lock = threading.Lock()
    release = threading.Event()

    def run(task, cancel):
        with lock:
            running.add(task["id"])
            observed.append(len(running))
            order.append(task["candidate"])
        release.wait(1)
        with lock:
            running.remove(task["id"])
        manager.store.update(task["id"], status="succeeded", ended=now())

    monkeypatch.setattr(manager, "run", run)
    for id in ("501", "500", "502"):
        manager.queue("diagram", id, runtime=runtime)
    manager.start()
    time.sleep(0.1)
    assert not order
    manager.configure({"paused": False})
    deadline = time.monotonic() + 1
    while len(order) < maximum and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(order) == maximum
    release.set()
    wait_tasks(manager)
    assert max(observed) == maximum
    assert order == ["501", "500", "502"]
    assert "stdout_tail" not in json.dumps(manager.queue_data())


def test_cancel_batch_restart_and_persisted_pause(manager):
    manager.configure({"paused": True})
    batch = submit(manager, request(["500", "501"]))
    for id in batch["task_ids"]:
        manager.cancel_task(id)
    assert all(t["status"] == "canceled" for t in manager.store.tasks())
    task = manager.queue("diagram", "502")
    restarted = Manager(manager.config, manager.candidates)
    assert restarted.settings["paused"]
    assert restarted.store.get(task["id"])["status"] == "queued"
    assert restarted.store.batch(batch["id"]) == batch


def test_queue_memory_budget_allows_ninety_percent_and_survives_restart(manager):
    limit = 16 * GIB * 9 // 10
    assert manager.configure({"memory_bytes": limit})["memory_bytes"] == limit
    with pytest.raises(ValueError, match="90%") as error:
        manager.configure({"memory_bytes": limit + 1})
    assert "14.40 GiB" in str(error.value)
    assert f"{limit} bytes" in str(error.value)
    assert manager.settings["memory_bytes"] == limit
    restarted = Manager(manager.config, manager.candidates)
    assert restarted.settings["memory_bytes"] == limit
    restarted.close()


def test_queue_default_memory_budget_on_small_hosts(manager, monkeypatch):
    monkeypatch.setattr("dashboard.tasks.memory_ceiling", lambda: 4 * GIB)
    manager.store.db.execute("DELETE FROM settings")
    manager.store.db.commit()
    restarted = Manager(manager.config, manager.candidates)
    assert restarted.settings["memory_bytes"] == 4 * GIB * 9 // 10
    restarted.close()


def test_container_missing_and_image_changed_do_not_fallback(
    manager, monkeypatch, tmp_path
):
    with pytest.raises(ValueError, match="SIF"):
        manager.queue("diagram", "500", runtime="apptainer")
    assert not manager.store.tasks()
    image = tmp_path / "image.sif"
    image.write_bytes(b"original")
    manager.config.image = image
    identity = manager.runtimes.image_identity()
    image.write_bytes(b"changed")
    with pytest.raises(ValueError, match="image changed"):
        manager.runtimes.wrap(
            ["pw.x"], tmp_path, {"cpus": 1, "memory_bytes": GIB}, identity
        )
    assert not manager.store.tasks()
    with pytest.raises(ValueError, match="budget"):
        manager.resources("apptainer", 1, 20, None)


def test_container_argv_and_no_host_mpi(manager, monkeypatch, tmp_path):
    monkeypatch.setattr("dashboard.runtimes.compute_cpus", lambda: [2, 4, 6])
    manager.config.image = tmp_path / "image.sif"
    manager.config.image.write_bytes(b"image")
    monkeypatch.setattr(
        "dashboard.runtimes.shutil.which", lambda _: "/usr/bin/apptainer"
    )
    command = manager.runtimes.wrap(
        ["mpirun", "-np", "2", "pw.x", "-in", "input.in"],
        tmp_path,
        {"cpus": 2, "memory_bytes": 4 * GIB},
        manager.runtimes.image_identity(),
    )
    assert command[:2] == ["/usr/bin/apptainer", "exec"]
    assert (
        command[command.index("--memory") + 1]
        == command[command.index("--memory-swap") + 1]
        == str(4 * GIB)
    )
    assert command[-6:] == ["mpirun", "-np", "2", "pw.x", "-in", "input.in"]
    assert "--pid" in command and "--cleanenv" in command
    assert command[command.index("--cpuset-cpus") + 1] == "2,4,6"


def test_cpu_reservation_caps_saved_budget_and_rejects_full_host(manager, monkeypatch):
    monkeypatch.setattr("dashboard.cpu.os.sched_getaffinity", lambda _: set(range(8)))
    manager.store.set_settings({**manager.settings, "cpus": 8})
    restarted = Manager(manager.config, manager.candidates)
    try:
        assert restarted.settings["cpus"] == 7
        assert restarted.queue_data()["cpu_capacity"] == 7
        with pytest.raises(ValueError, match="reserved for web requests"):
            restarted.configure({"cpus": 8})
        assert restarted.configure({"cpus": 7})["cpus"] == 7
    finally:
        restarted.close()


def test_oversized_waiting_job_fails_instead_of_blocking_queue(manager, monkeypatch):
    manager.configure({"cpus": 2})
    batch = submit(manager, request(["500"], processes=2))
    task = manager.store.get(batch["task_ids"][0])
    manager.store.update(task["id"], resources={**task["resources"], "cpus": 2})
    monkeypatch.setattr("dashboard.tasks.compute_cpus", lambda: [1])
    restarted = Manager(manager.config, manager.candidates)
    try:
        restarted.start()
        wait_tasks(restarted)
        task = restarted.store.get(task["id"])
        assert task["status"] == "failed"
        assert "retry with fewer CPUs" in task["error"]
    finally:
        restarted.close()


def test_api_cancel_running_batch(manager, monkeypatch, tmp_path):
    executable = tmp_path / "slow-diagram"
    executable.write_text(
        f"#!{sys.executable}\nimport time\nprint('started', flush=True)\ntime.sleep(30)\n"
    )
    executable.chmod(0o755)
    app = create_app(manager.config)
    runner = app.state.manager
    runner.config.python = str(executable)
    batch = submit(runner, request(["500", "501"]))
    unrelated = runner.queue("diagram", "502")
    with TestClient(app) as client:
        running_id = batch["task_ids"][0]
        log = runner.config.artifacts / running_id / "stdout.log"
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if log.exists() and "started" in log.read_text():
                break
            time.sleep(0.01)
        else:
            raise AssertionError("Batch job did not start")
        # Leave unrelated waiting work paused while cancellation finishes.
        runner.configure({"paused": True})
        assert client.post(f"/api/batches/{batch['id']}/cancel").status_code == 200
        assert runner.store.get(running_id)["stop_requested"]
        assert runner.store.get(batch["task_ids"][1])["status"] == "canceled"
        deadline = time.monotonic() + 3
        while runner.store.get(running_id)["status"] == "running":
            assert time.monotonic() < deadline, "Running batch job did not stop"
            time.sleep(0.01)
        assert runner.store.get(running_id)["status"] == "canceled"
        assert runner.store.get(unrelated["id"])["status"] == "queued"
        assert client.post(f"/api/batches/{batch['id']}/cancel").status_code == 200
        assert client.post("/api/batches/missing/cancel").status_code == 404


def test_api_clear_queue_preserves_running_jobs_and_history(manager, monkeypatch):
    app = create_app(manager.config)
    runner = app.state.manager
    monkeypatch.setattr(runner, "start", lambda: None)
    batch = submit(runner, request(["500", "501"]))
    standalone = runner.queue("diagram", "502")
    running_id = batch["task_ids"][0]
    runner.store.update(running_id, status="running", started=now())
    cancel = threading.Event()
    runner.running[running_id] = (None, cancel, runner.store.get(running_id))
    completed = {**standalone, "id": "completed", "status": "succeeded"}
    runner.store.put(completed)
    with TestClient(app) as client:
        assert client.post("/api/queue/clear").json() == {"canceled_count": 2}
        assert not cancel.is_set()
        assert runner.store.get(running_id)["status"] == "running"
        assert runner.store.get(completed["id"]) == completed
        for id in (batch["task_ids"][1], standalone["id"]):
            task = runner.store.get(id)
            assert task["status"] == "canceled" and task["ended"]
        assert runner.store.batch(batch["id"]) == batch
        assert runner.batches.retry(batch["id"])["candidates"] == ["501"]
        assert client.post("/api/queue/clear").json() == {"canceled_count": 0}
        assert (
            client.post(
                "/api/queue/clear", headers={"origin": "https://evil.test"}
            ).status_code
            == 403
        )
    restarted = Manager(manager.config, manager.candidates)
    assert restarted.store.get(standalone["id"])["status"] == "canceled"
    restarted.close()


def test_api_batch_queue_and_origins(manager, monkeypatch):
    app = create_app(manager.config)
    monkeypatch.setattr(app.state.manager, "start", lambda: None)
    with TestClient(app) as client:
        body = request(["501", "500"])
        review = client.post("/api/batches/preview", json=body).json()
        assert client.get("/api/data").json()["tasks"] == []
        submitted = client.post(
            "/api/batches", json={**body, "expected": review["entries"], "id": "api"}
        ).json()
        assert len(submitted["task_ids"]) == 2
        data = client.get("/api/queue").json()
        assert data["batches"][0]["id"] == "api"
        assert all("stdout_tail" not in t for t in data["tasks"])
        assert client.post("/api/queue/settings", json={"paused": True}).json()[
            "paused"
        ]
        assert client.post("/api/batches/api/cancel").status_code == 200
        assert client.get("/api/batches/api/retry").json()["candidates"] == [
            "501",
            "500",
        ]
        assert (
            client.post(
                "/api/batches", json=body, headers={"origin": "https://evil.test"}
            ).status_code
            == 403
        )
        assert client.get("/api/tasks/missing/logs").status_code == 400


def test_resource_probe_requires_actual_memory_and_swap_limits(tmp_path, monkeypatch):
    from dashboard.container_runner import limits

    (tmp_path / "memory.max").write_text(str(GIB))
    (tmp_path / "memory.swap.max").write_text("0")
    monkeypatch.setattr("dashboard.container_runner.cgroup", lambda: tmp_path)
    assert limits(GIB)["verified"]
    (tmp_path / "memory.max").write_text("max")
    assert not limits(GIB)["verified"]
    (tmp_path / "memory.max").write_text(str(GIB))
    (tmp_path / "memory.swap.max").write_text(str(GIB))
    assert not limits(GIB)["verified"]


def test_recover_partial_batch_acceptance(manager):
    batch = submit(manager, request(["500", "501"]))
    # Simulate a crash after the first task was saved but before its entry was saved.
    manager.store.put_batch(
        {**batch, "entries": [], "task_ids": [], "candidates": ["500", "501", "502"]}
    )
    recovered = Manager(manager.config, manager.candidates)
    batch = recovered.store.batch(batch["id"])
    assert len(batch["task_ids"]) == 2
    assert batch["entries"][-1]["status"] == "unavailable"
    assert recovered.batches.retry(batch["id"])["candidates"] == ["502"]


def test_smiles_and_pseudo_preflight_does_not_block_other_entries(manager, monkeypatch):
    monkeypatch.setattr(
        manager.runtimes,
        "chemistry_probe",
        lambda *a, **k: SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps({"500": None, "501": ["Unknown"], "502": ["H"]}),
        ),
    )
    review = manager.batches.review(**request(["500", "501", "502"], "prepare"))
    assert [e["status"] for e in review["entries"]] == [
        "unavailable",
        "unavailable",
        "eligible",
    ]
    assert "SMILES" in review["entries"][0]["reason"]
    assert "pseudopotentials" in review["entries"][1]["reason"]


def test_cgroup_oom_evidence_and_unknown_kill_are_distinct(tmp_path):
    from dashboard.processes import execute

    report = {
        "verified": True,
        "cgroup": "/sys/fs/cgroup/pfas-test-nonexistent",
        "events_before": "oom_kill 0\n",
        "events_after": "oom_kill 1\n",
        "peak_memory_bytes": 123456,
    }
    (tmp_path / "resource.json").write_text(json.dumps(report))
    samples = []
    code, state, error = execute(
        [sys.executable, "-c", "pass"],
        tmp_path,
        threading.Event(),
        container=True,
        on_usage=samples.append,
        on_start=lambda process: process.wait(timeout=2),
    )
    assert code == 0 and state == "failed" and "Memory limit" in error
    assert samples[-1]["peak_memory_bytes"] >= 123456
    (tmp_path / "resource.json").unlink()
    _, state, error = execute(
        [sys.executable, "-c", "import os,signal;os.kill(os.getpid(),signal.SIGKILL)"],
        tmp_path,
        threading.Event(),
        container=True,
    )
    assert state == "failed" and "Memory limit" not in error


def test_worker_finalization_error_releases_slot(manager, monkeypatch):
    order = []

    def run(task, cancel):
        order.append(task["candidate"])
        if task["candidate"] == "500":
            raise OSError("artifact registration failed")
        manager.store.update(task["id"], status="succeeded", ended=now())

    monkeypatch.setattr(manager, "run", run)
    manager.queue("diagram", "500")
    manager.queue("diagram", "501")
    manager.start()
    wait_tasks(manager)
    assert order == ["500", "501"]
    assert manager.store.tasks()[0]["status"] == "succeeded"
    assert manager.store.tasks()[1]["status"] == "failed"


def test_remote_batch_rejected_before_work(manager):
    with pytest.raises(NotImplementedError):
        manager.batches.review(**request(["500"], target="slurm"))
    with pytest.raises(NotImplementedError):
        manager.queue("prepare", "500", target="slurm")
    assert manager.store.tasks() == []


@pytest.mark.parametrize("system", ["candidate", "complex"])
def test_bulk_ram_estimates_order_resources_snapshots_and_results(
    manager, monkeypatch, system
):
    manager.config.pw = sys.executable
    manager.config.mpi = sys.executable
    originals = []
    for candidate in ["501", "500"]:
        path = prepare_qe(manager, candidate)
        if system == "complex":
            alternate = path.with_name("complex.in")
            alternate.write_text(path.read_text())
            path = alternate
        originals.append(path)
    body = request(
        ["501", "500", "502"], "estimate_ram", system=system, processes=2, id="ram-bulk"
    )
    preview = manager.batches.review(**body)
    assert preview["resources"]["timeout"] == 120
    assert preview["resources"]["cpus"] == 2
    assert [entry["status"] for entry in preview["entries"]] == [
        "eligible",
        "eligible",
        "unavailable",
    ]
    assert all(
        "nstep=0" in entry["input"] and entry["preview_id"]
        for entry in preview["entries"][:2]
    )
    batch = manager.batches.submit({**body, "expected": preview["entries"]})
    tasks = [manager.store.get(id) for id in batch["task_ids"]]
    assert [t["candidate"] for t in tasks] == ["501", "500"]
    assert all(
        t["kind"] == "estimate_ram" and t["system"] == system and t["processes"] == 2
        for t in tasks
    )
    for original, task in zip(originals, tasks):
        assert "nstep=0" not in original.read_text()
        original.unlink()
        assert (
            "nstep=0"
            in (manager.config.artifacts / task["id"] / "input.in").read_text()
        )
    (manager.config.pseudos / "H.UPF").unlink()

    def execute(command, directory, *args, **kwargs):
        assert command[1:3] == ["-np", "2"]
        assert (directory / "Pseudopotentials/H.UPF").read_text() == "hydrogen"
        (directory / "stdout.log").write_text(
            "Estimated max dynamical RAM per process > 2.0 MB\n"
        )
        return 255, "failed", ""

    monkeypatch.setattr("dashboard.tasks.execute", execute)
    manager.start()
    wait_tasks(manager)
    assert all(manager.store.get(t["id"])["status"] == "succeeded" for t in tasks)
    assert all("evidence" not in manager.store.get(t["id"]) for t in tasks)
    queued = manager.queue_data()["tasks"]
    assert all(
        t["ram_estimate"]["per_process"]["value"] == "2.0"
        for t in queued
        if t["kind"] == "estimate_ram"
    )
    assert manager.batches.submit({**body, "expected": preview["entries"]}) == batch


@pytest.mark.parametrize("change", ["source", "pseudo", "processes", "missing_preview"])
def test_bulk_ram_requires_original_preview(manager, change):
    manager.config.pw = sys.executable
    manager.config.mpi = sys.executable
    source = prepare_qe(manager, "500")
    source.write_text(source.read_text().replace("&CONTROL", "&CONTROL\n nstep=1,"))
    body = request(["500"], "estimate_ram")
    preview = manager.batches.review(**body)
    if change == "source":
        source.write_text(source.read_text().replace("nstep=1", "nstep=2"))
        current = manager.batches.review(**body)["entries"][0]
        assert current["input_hash"] == preview["entries"][0]["input_hash"]
        assert current["source_hash"] != preview["entries"][0]["source_hash"]
    elif change == "pseudo":
        (manager.config.pseudos / "H.UPF").write_text("edited")
    elif change == "processes":
        body["processes"] = 2
    else:
        preview["entries"][0].pop("preview_id")
    batch = manager.batches.submit({**body, "expected": preview["entries"]})
    assert not batch["task_ids"]
    assert batch["entries"][0]["status"] == "unavailable"
    assert "preview" in batch["entries"][0]["reason"]


def test_bulk_ram_completed_matching_and_retry(manager):
    manager.config.pw = sys.executable
    manager.config.mpi = sys.executable
    source = prepare_qe(manager, "500")
    body = request(["500"], "estimate_ram", timeout=35)
    batch = submit(manager, body)
    task = manager.store.get(batch["task_ids"][0])
    assert task["timeout"] == 35
    manager.store.update(
        task["id"],
        status="succeeded",
        ram_estimate={
            "per_process": {"value": "2", "unit": "MB", "bytes": 2 * 1024**2},
            "total": None,
        },
    )
    assert manager.batches.review(**body)["entries"][0]["status"] == "completed"
    assert (
        manager.batches.review(**{**body, "processes": 2})["entries"][0]["status"]
        == "eligible"
    )
    source.write_text(source.read_text() + "! edited\n")
    assert manager.batches.review(**body)["entries"][0]["status"] == "eligible"
    manager.store.update(task["id"], status="canceled")
    retry = manager.batches.retry(batch["id"])
    assert retry["kind"] == "estimate_ram" and retry["candidates"] == ["500"]
    fresh = submit(manager, retry)
    assert fresh["task_ids"][0] != task["id"]


def test_api_bulk_ram_preview_submission_cancel_and_retry(manager, monkeypatch):
    manager.config.pw = sys.executable
    source = prepare_qe(manager, "500")
    app = create_app(manager.config)
    monkeypatch.setattr(app.state.manager, "start", lambda: None)
    with TestClient(app) as client:
        body = request(["500", "501"], "estimate_ram")
        response = client.post("/api/batches/preview", json=body)
        assert response.status_code == 200
        preview = response.json()
        # Submit exactly the fields the bulk controls send.
        expected = [
            {
                k: e[k]
                for k in (
                    "candidate",
                    "status",
                    "input_hash",
                    "source_hash",
                    "preview_id",
                    "pseudopotentials",
                )
                if k in e
            }
            for e in preview["entries"]
        ]
        response = client.post(
            "/api/batches", json={**body, "expected": expected, "id": "ram-api"}
        )
        assert response.status_code == 200
        assert len(response.json()["task_ids"]) == 1
        queued = client.get("/api/queue").json()
        task = next(t for t in queued["tasks"] if t["kind"] == "estimate_ram")
        assert task["resources"]["timeout"] == 120
        assert task["version"] == "Prepared default"
        assert source.exists()
        assert client.post("/api/batches/ram-api/cancel").status_code == 200
        retry = client.get("/api/batches/ram-api/retry").json()
        assert retry["kind"] == "estimate_ram"
        assert retry["candidates"] == ["500", "501"]


def test_bulk_ram_rerun_completed_requires_review_and_still_skips_active(manager):
    manager.config.pw = sys.executable
    prepare_qe(manager, "500")
    body = request(["500"], "estimate_ram")
    batch = submit(manager, body)
    manager.store.update(
        batch["task_ids"][0],
        status="succeeded",
        ram_estimate={
            "per_process": {"value": "2", "unit": "MB", "bytes": 2 * 1024**2},
            "total": None,
        },
    )
    previous = manager.batches.review(**body)
    assert previous["entries"][0]["status"] == "completed"
    rerun = {**body, "rerun_completed": True}
    assert manager.batches.review(**rerun)["entries"][0]["status"] == "eligible"
    rejected = manager.batches.submit({**rerun, "expected": previous["entries"]})
    assert rejected["task_ids"] == []
    assert "preview" in rejected["entries"][0]["reason"]
    fresh = submit(manager, rerun)
    assert fresh["rerun_completed"]
    assert fresh["task_ids"][0] != batch["task_ids"][0]
    assert manager.batches.review(**rerun)["entries"][0]["status"] == "active"


def test_rerun_completed_flag_does_not_change_other_batch_actions(manager):
    batch = submit(manager, request(["500"]))
    task = manager.store.get(batch["task_ids"][0])
    (manager.config.artifacts / task["id"] / "diagram.png").write_bytes(b"png")
    manager.store.update(task["id"], status="succeeded")
    assert (
        manager.batches.review(**request(["500"], rerun_completed=True))["entries"][0][
            "status"
        ]
        == "completed"
    )


def test_api_bulk_ram_rerun_checkbox(manager, monkeypatch):
    manager.config.pw = sys.executable
    prepare_qe(manager, "500")
    body = request(["500"], "estimate_ram")
    batch = submit(manager, body)
    manager.store.update(
        batch["task_ids"][0],
        status="succeeded",
        ram_estimate={
            "per_process": {"value": "2", "unit": "MB", "bytes": 2 * 1024**2},
            "total": None,
        },
    )
    app = create_app(manager.config)
    monkeypatch.setattr(app.state.manager, "start", lambda: None)
    with TestClient(app) as client:
        assert (
            client.post("/api/batches/preview", json=body).json()["entries"][0][
                "status"
            ]
            == "completed"
        )
        rerun = {**body, "rerun_completed": True}
        preview = client.post("/api/batches/preview", json=rerun).json()
        assert preview["entries"][0]["status"] == "eligible"
        response = client.post(
            "/api/batches",
            json={**rerun, "expected": preview["entries"], "id": "rerun-api"},
        )
        assert response.status_code == 200
        assert len(response.json()["task_ids"]) == 1


def test_999_ram_previews_share_history_and_pseudo_reads(manager, monkeypatch):
    from dashboard.preparation import digest

    candidates = [str(i) for i in range(999)]
    for candidate in candidates:
        manager.candidates[candidate] = {"id": candidate, "smiles": "O"}
        prepare_qe(manager, candidate)
    # A missing newer artifact must still fall back to the older prepared input.
    manager.store.put(
        {
            "id": "missing-artifact",
            "candidate": "0",
            "kind": "prepare",
            "system": "candidate",
            "status": "succeeded",
        }
    )
    reads = []
    hashes = []
    tasks = manager.store.tasks

    def read_tasks():
        reads.append(1)
        return tasks()

    def hash_pseudo(path):
        hashes.append(path)
        return digest(path)

    monkeypatch.setattr(manager.store, "tasks", read_tasks)
    monkeypatch.setattr("dashboard.tasks.digest", hash_pseudo)
    monkeypatch.setattr(manager.runtimes, "verify", lambda: "image-hash")
    monkeypatch.setattr(manager.runtimes, "image_identity", lambda: "image-hash")
    body = request(
        candidates, "estimate_ram", runtime="apptainer", memory_gib=2, processes=1
    )
    preview = manager.batches.review(**body)
    assert reads == [1]
    assert hashes == [manager.config.pseudos / "H.UPF"]
    assert [e["candidate"] for e in preview["entries"]] == candidates
    assert all(e["status"] == "eligible" for e in preview["entries"])
    assert len({e["preview_id"] for e in preview["entries"]}) == 999
    assert preview["resources"]["memory_bytes"] == 2 * GIB
    assert preview["resources"]["cpus"] == 1
    # Cached hashes expire with the review, including same-size pseudo edits.
    (manager.config.pseudos / "H.UPF").write_text("modified")
    fresh = manager.batches.review(**{**body, "candidates": ["0"]})
    assert (
        fresh["entries"][0]["pseudopotentials"]
        != preview["entries"][0]["pseudopotentials"]
    )


def test_queue_default_input_uses_targeted_history_and_falls_back(manager, monkeypatch):
    original = prepare_qe(manager, "500")
    prepare_qe(manager, "501")
    manager.store.put(
        {
            "id": "missing-newer-input",
            "candidate": "500",
            "kind": "prepare",
            "system": "candidate",
            "status": "succeeded",
        }
    )

    def no_full_history():
        raise AssertionError("Single-job lookup must not reload full history")

    monkeypatch.setattr(manager.store, "tasks", no_full_history)
    assert manager.input("500", "candidate") == original
    tfa = original.with_name("tfa.in")
    tfa.write_text(original.read_text())
    assert manager.input("tfa", "tfa") == tfa
    with pytest.raises(ValueError, match="Prepare inputs first"):
        manager.input("502", "candidate")
    monkeypatch.undo()
