import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

import psutil

from .persistence import now


def terminate(process):
    # Capture descendants before the leader exits; MPI can create other groups.
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        children = []
    for child in children:
        try:
            child.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pass
    except ProcessLookupError:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    for child in children:
        try:
            child.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    process.wait()


def execute(
    command,
    directory,
    cancel,
    timeout=None,
    on_start=lambda p: None,
    env=None,
    on_usage=lambda value: None,
    container=False,
):
    start = time.monotonic()
    with (
        (directory / "stdout.log").open("wb") as out,
        (directory / "stderr.log").open("wb") as err,
    ):
        metadata = directory / "entrypoint.log"
        header = [f"Task: {directory.name}"]
        if metadata.is_file():
            header.extend(
                line
                for line in metadata.read_text().splitlines()
                if line.startswith(
                    (
                        "Input hash:",
                        "Source hash:",
                        "Image hash:",
                        "Runtime:",
                        "Resources:",
                    )
                )
            )
        for name, log in (("stdout", out), ("stderr", err)):
            for line in [*header, f"Stream: {name}"]:
                log.write(f"[{now()}] {line}\n".encode())
            log.flush()
        process = subprocess.Popen(
            command,
            cwd=directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env={
                **(env or os.environ),
                "OMP_NUM_THREADS": "1",
                "PYTHONUNBUFFERED": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "NUMEXPR_NUM_THREADS": "1",
            },
        )
        streams = [
            (process.stdout, out, bytearray()),
            (process.stderr, err, bytearray()),
        ]
        for stream, _, _ in streams:
            os.set_blocking(stream.fileno(), False)

        def drain(final=False):
            for stream, log, buffer in streams:
                # Bound each drain so noisy output cannot starve timeout/cancel checks.
                for _ in range(16):
                    try:
                        chunk = os.read(stream.fileno(), 65536)
                    except BlockingIOError:
                        break
                    if not chunk:
                        break
                    buffer.extend(chunk)
                    while b"\n" in buffer or len(buffer) >= 65536:
                        end = buffer.find(b"\n")
                        size = end + 1 if end >= 0 else 65536
                        log.write(f"[{now()}] ".encode() + bytes(buffer[:size]))
                        del buffer[:size]
                if final:
                    if buffer:
                        log.write(f"[{now()}] ".encode() + bytes(buffer) + b"\n")
                        buffer.clear()
                    stream.close()
                log.flush()

        on_start(process)
        peak = 0
        group_peak = 0
        previous_sample = 0
        memory_events = None
        group_path = None

        def sample():
            nonlocal peak, group_peak, memory_events, group_path
            rss = 0
            try:
                parent = psutil.Process(process.pid)
                for child in [parent, *parent.children(recursive=True)]:
                    try:
                        rss += child.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            peak = max(peak, rss)
            value = {
                "current_memory_bytes": rss,
                "peak_memory_bytes": peak,
                "measurement": "process-tree RSS",
            }
            if container:
                try:
                    report = json.loads((directory / "resource.json").read_text())
                    group_path = Path(report["cgroup"]).resolve()
                    if (
                        not group_path.is_relative_to("/sys/fs/cgroup")
                        or not report["verified"]
                    ):
                        raise ValueError("Invalid resource metadata")
                    events = (
                        (group_path / "memory.events").read_text()
                        if group_path.exists()
                        else report.get("events_after", "")
                    )
                    before = dict(
                        line.split() for line in report["events_before"].splitlines()
                    )
                    after = dict(line.split() for line in events.splitlines())
                    if int(after.get("oom_kill", 0)) > int(before.get("oom_kill", 0)):
                        memory_events = "Memory limit exceeded (cgroup OOM kill)"
                    if group_path.exists():
                        group_peak = max(
                            group_peak, int((group_path / "memory.peak").read_text())
                        )
                        value["current_memory_bytes"] = int(
                            (group_path / "memory.current").read_text()
                        )
                    group_peak = max(group_peak, report.get("peak_memory_bytes", 0))
                    if not group_path.exists():
                        value["current_memory_bytes"] = 0
                    value.update(
                        peak_memory_bytes=group_peak, measurement="cgroup memory"
                    )
                except (OSError, ValueError, KeyError):
                    pass
            try:
                on_usage(value)
            except Exception:
                terminate(process)
                raise

        while process.poll() is None:
            drain()
            if time.monotonic() - previous_sample > 0.25:
                sample()
                previous_sample = time.monotonic()
            if memory_events:
                terminate(process)
                drain(final=True)
                sample()
                return process.returncode, "failed", memory_events
            if cancel.is_set() or (timeout and time.monotonic() - start > timeout):
                terminate(process)
                drain(final=True)
                sample()
                return (
                    process.returncode,
                    "canceled" if cancel.is_set() else "failed",
                    "Stopped" if cancel.is_set() else "Timed out",
                )
            time.sleep(0.05)
        drain(final=True)
        sample()
        return (
            process.returncode,
            "succeeded" if process.returncode == 0 and not memory_events else "failed",
            memory_events or "",
        )


def evidence(text, calculation="scf"):
    energies = re.findall(r"!\s+total energy\s*=\s*([-+\d.EeDd]+)\s+Ry", text)
    completed = "JOB DONE." in text
    scf = "convergence has been achieved" in text
    relax = (
        "End of BFGS Geometry Optimization" in text or "bfgs converged" in text.lower()
    )
    failed = "convergence NOT achieved" in text or "Error in routine" in text
    confirmed = (
        completed
        and scf
        and not failed
        and (calculation not in ("relax", "vc-relax") or relax)
    )
    return {
        "job_done": completed,
        "scf_converged": scf,
        "relaxation_completed": relax,
        "confirmed": confirmed,
        "energy_ry": float(energies[-1].replace("D", "E").replace("d", "e"))
        if energies
        else None,
        "provisional": not confirmed,
    }


def tail(path, limit=16000):
    if not path.exists():
        return ""
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - limit))
        return stream.read(limit).decode(errors="replace")
