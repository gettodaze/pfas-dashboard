import argparse
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from .config import ROOT, Config
from .export import export
from .processes import terminate


def main():
    parser = argparse.ArgumentParser(description="PFAS React/FastAPI dashboard")
    parser.add_argument(
        "action", nargs="?", choices=["serve", "export"], default="serve"
    )
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--output", default=str(ROOT / "web/public"))
    args = parser.parse_args()
    frontend = ROOT / "web/frontend"
    subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=frontend, check=True)
    if args.action == "export":
        output = Path(args.output).resolve()
        if (
            output == ROOT / "web/frontend/dist"
            or output == ROOT / "web/snapshot"
            or output in ROOT.parents
            or output == ROOT
        ):
            parser.error("Export destination must be a separate site directory")
        with TemporaryDirectory(prefix="pfas-snapshot-build-") as build:
            subprocess.run(
                ["npm", "run", "build", "--", "--outDir", build],
                cwd=frontend,
                check=True,
                env={**os.environ, "PFAS_DATA_MODE": "snapshot"},
            )
            export(Config(), ROOT / "web/snapshot")
            shutil.copytree(build, output, dirs_exist_ok=True)
        shutil.copytree(ROOT / "web/snapshot", output / "snapshot", dirs_exist_ok=True)
        print(f"Snapshot site exported to {output}; no tasks started")
        return
    vite = None
    if args.dev:
        vite = subprocess.Popen(
            ["npm", "run", "dev", "--", "--host", args.host],
            cwd=frontend,
            start_new_session=True,
            env={
                **os.environ,
                "PFAS_DATA_MODE": "live",
                "PFAS_API_PORT": str(args.port),
                "PFAS_API_HOST": args.host,
            },
        )
    else:
        subprocess.run(
            ["npm", "run", "build"],
            cwd=frontend,
            check=True,
            env={**os.environ, "PFAS_DATA_MODE": "live"},
        )
    try:
        import uvicorn

        from .server import create_app

        config = Config()
        print(f"Preparation interpreter: {config.prepare_python}", flush=True)
        uvicorn.run(create_app(config), host=args.host, port=args.port)
    finally:
        if vite:
            terminate(vite)


if __name__ == "__main__":
    main()
