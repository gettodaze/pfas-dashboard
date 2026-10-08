"""Leave one allowed CPU available to the dashboard and other host work."""

import os
import sys


def compute_cpus():
    allowed = (
        sorted(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else list(range(os.cpu_count() or 1))
    )
    return allowed[1:] if len(allowed) > 1 else allowed


if __name__ == "__main__":
    # Run in a separate interpreter: preexec_fn is unsafe in a threaded server.
    cpus = [int(cpu) for cpu in sys.argv[1].split(",")]
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, cpus)
    if hasattr(os, "nice"):
        os.nice(10)
    os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
