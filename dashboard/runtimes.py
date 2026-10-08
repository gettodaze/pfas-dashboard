"""Local execution adapters; the job specification is independent of the scheduler."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from .config import ROOT
from .cpu import compute_cpus

GIB = 1024**3


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def memory_ceiling():
    import psutil

    ceiling = psutil.virtual_memory().total
    try:
        relative = Path("/proc/self/cgroup").read_text().split("0::", 1)[1].strip()
        path = Path("/sys/fs/cgroup") / relative.lstrip("/")
        for parent in [path, *path.parents]:
            if not parent.is_relative_to("/sys/fs/cgroup"):
                break
            value = (parent / "memory.max").read_text().strip()
            if value != "max":
                ceiling = min(ceiling, int(value))
    except (OSError, ValueError, IndexError):
        pass
    return ceiling


class Runtimes:
    def __init__(self, config):
        self.config = config
        self.verified = None

    def availability(self):
        executable = shutil.which(self.config.apptainer)
        image = self.config.image
        verified = bool(
            self.verified
            and image
            and image.is_file()
            and self.verified[0]
            == (str(image), image.stat().st_size, image.stat().st_mtime_ns)
        )
        return {
            "native": {
                "available": True,
                "hard_memory_limit": False,
                "parallel": False,
            },
            "apptainer": {
                "available": bool(executable and image and image.is_file()),
                "hard_memory_limit": verified,
                "parallel": verified,
                "message": "Verify memory isolation with a batch preview"
                if executable and image and image.is_file()
                else "Install Apptainer and configure PFAS_APPTAINER_IMAGE",
            },
        }

    def image_identity(self):
        image = self.config.image
        if not image or not image.is_file():
            raise ValueError("Set PFAS_APPTAINER_IMAGE to a built SIF image")
        stat = image.stat()
        key = (str(image), stat.st_size, stat.st_mtime_ns)
        if self.verified and self.verified[0] == key:
            return self.verified[1]
        return file_hash(image)

    def base(self, memory_bytes, cpus):
        executable = shutil.which(self.config.apptainer)
        if not executable:
            raise ValueError("Apptainer executable unavailable")
        return [
            executable,
            "exec",
            "--cleanenv",
            "--contain",
            "--pid",
            "--memory",
            str(memory_bytes),
            "--memory-swap",
            str(memory_bytes),
            "--cpus",
            str(cpus),
            "--cpuset-cpus",
            ",".join(map(str, compute_cpus())),
        ]

    def verify(self):
        identity = self.image_identity()
        image = self.config.image
        assert image is not None
        stat = image.stat()
        key = (str(image), stat.st_size, stat.st_mtime_ns)
        if self.verified and self.verified[0] == key:
            return identity
        limit = 128 * 1024**2
        command = [
            *self.base(limit, 1),
            "--bind",
            f"{ROOT}:{ROOT}:ro",
            "--bind",
            "/sys/fs/cgroup:/sys/fs/cgroup:ro",
            str(image),
            "/opt/venv/bin/python",
            str(ROOT / "dashboard/container_runner.py"),
            "--probe",
            str(limit),
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=30, check=False
        )
        if result.returncode:
            raise ValueError(
                "Apptainer memory-isolation probe failed: " + result.stderr[-1000:]
            )
        try:
            report = json.loads(result.stdout)
            if not report["verified"]:
                raise ValueError("Hard memory and swap limits were not verified")
        except (KeyError, json.JSONDecodeError) as error:
            raise ValueError("Invalid Apptainer memory-isolation probe") from error
        self.verified = (key, identity)
        return identity

    def wrap(self, command, directory, resources, image_hash):
        if self.image_identity() != image_hash:
            raise ValueError("Container image changed; preview and retry explicitly")
        binds = {ROOT, self.config.artifacts, self.config.pseudos}
        args = self.base(resources["memory_bytes"], resources["cpus"])
        for path in sorted(binds):
            args += ["--bind", f"{path}:{path}:ro"]
        args += [
            "--bind",
            f"{directory}:{directory}:rw",
            "--bind",
            "/sys/fs/cgroup:/sys/fs/cgroup:ro",
            "--pwd",
            str(directory),
        ]
        # Match the image's MPI with its QE binary; never inject host MPI/Python.
        for key in (
            "OMP_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "MKL_NUM_THREADS",
            "NUMEXPR_NUM_THREADS",
        ):
            args += ["--env", f"{key}=1"]
        args += [
            str(self.config.image),
            "/opt/venv/bin/python",
            str(ROOT / "dashboard/container_runner.py"),
            "--metadata",
            str(directory / "resource.json"),
            "--limit",
            str(resources["memory_bytes"]),
            "--",
            *command,
        ]
        return args

    def chemistry_probe(self, runtime, code, args=(), input_text=None):
        if runtime == "native":
            python = self.config.prepare_python
            command = [python, "-c", code, *args]
            env = {
                **os.environ,
                "PATH": str(Path(python).absolute().parent)
                + os.pathsep
                + os.environ["PATH"],
            }
        else:
            self.verify()
            command = [
                *self.base(512 * 1024**2, 1),
                str(self.config.image),
                "/opt/venv/bin/python",
                "-c",
                code,
                *args,
            ]
            env = None
        return subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
