"""Small container supervisor: verify enforcement before executing any job."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def cgroup():
    relative = Path("/proc/self/cgroup").read_text().split("0::", 1)[1].strip()
    return Path("/sys/fs/cgroup") / relative.lstrip("/")


def limits(limit):
    path = cgroup()
    maximum = (path / "memory.max").read_text().strip()
    swap = (path / "memory.swap.max").read_text().strip()
    return {
        "verified": maximum != "max" and 0 < int(maximum) <= limit and swap == "0",
        "cgroup": str(path),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--metadata")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    report = limits(args.probe or args.limit)
    if args.probe:
        print(json.dumps(report))
        return 0 if report["verified"] else 1
    if not report["verified"]:
        print("Hard memory isolation unavailable; job was not started", file=sys.stderr)
        return 1
    path = Path(report["cgroup"])
    before = (path / "memory.events").read_text()
    report["events_before"] = before
    report["host_pid"] = os.getpid()
    temporary = Path(args.metadata).with_suffix(".tmp")
    temporary.write_text(json.dumps(report))
    temporary.replace(args.metadata)
    command = args.command[1:] if args.command[0] == "--" else args.command
    code = subprocess.call(command)
    report["events_after"] = (path / "memory.events").read_text()
    report["peak_memory_bytes"] = int((path / "memory.peak").read_text())
    temporary.write_text(json.dumps(report))
    temporary.replace(args.metadata)
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    sys.exit(main())
