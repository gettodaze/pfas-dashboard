import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictInt

from .candidates import TFA, load
from .config import ROOT, Config
from .processes import tail
from .tasks import Manager


class Action(BaseModel):
    kind: str
    candidate: str
    system: str = "candidate"
    processes: StrictInt = 1
    target: str = "local"
    timeout: float | None = None
    expected_hash: str | None = None
    input_id: str | None = None
    preview_id: str | None = None
    expected_pseudos: dict[str, str] | None = None
    runtime: str = "native"
    memory_gib: float = 4


class BatchAction(BaseModel):
    target: str = "local"
    candidates: list[str]
    kind: str
    system: str = "candidate"
    runtime: str = "native"
    processes: StrictInt = 1
    memory_gib: float = 4
    timeout: float | None = None
    retry: bool = False
    rerun_completed: bool = False
    id: str | None = None
    image_hash: str | None = None
    expected: list[dict] = Field(default_factory=list)


class QueueSettings(BaseModel):
    paused: bool | None = None
    concurrency: StrictInt | None = None
    cpus: StrictInt | None = None
    memory_bytes: StrictInt | None = None


def create_app(config=None):
    manager = Manager(config or Config(), {c["id"]: c for c in [*load(), TFA]})

    @asynccontextmanager
    async def lifespan(app):
        manager.start()
        yield
        manager.close()

    app = FastAPI(lifespan=lifespan)
    app.state.manager = manager

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if request.headers.get("sec-fetch-site") == "cross-site" or (
                origin and urlparse(origin).netloc != request.headers.get("host")
            ):
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    {"detail": "Same-origin actions only"}, status_code=403
                )
        return await call_next(request)

    @app.get("/api/data")
    def data():
        return manager.data()

    @app.post("/api/preview")
    def preview(action: Action):
        return checked(
            lambda: manager.preview(
                action.candidate,
                action.system,
                action.processes,
                action.target,
                action.runtime,
                action.input_id,
                action.kind,
            )
        )

    @app.post("/api/tasks")
    def queue(action: Action):
        return checked(lambda: manager.queue(**action.model_dump()))

    @app.post("/api/batches/preview")
    def batch_preview(action: BatchAction):
        review = checked(lambda: manager.batches.review(**action.model_dump()))
        # Bulk review has no geometry viewer; keep coordinates in single-job previews.
        review["entries"] = [
            {key: value for key, value in entry.items() if key != "geometry"}
            for entry in review["entries"]
        ]
        return review

    @app.post("/api/batches")
    def batch_submit(action: BatchAction):
        return checked(lambda: manager.batches.submit(action.model_dump()))

    @app.get("/api/queue")
    def queue_view():
        return manager.queue_data()

    @app.post("/api/queue/settings")
    def queue_settings(settings: QueueSettings):
        return checked(
            lambda: manager.configure(settings.model_dump(exclude_none=True))
        )

    @app.post("/api/queue/clear")
    def queue_clear():
        return manager.clear_queue()

    @app.post("/api/batches/{id}/cancel")
    def batch_cancel(id: str):
        with manager.guard:
            batch = manager.store.batch(id)
            if not batch:
                raise HTTPException(404, "Unknown batch")
            for task_id in batch["task_ids"]:
                manager.cancel_task(task_id)
        return {"status": "stop requested"}

    @app.get("/api/batches/{id}/retry")
    def batch_retry(id: str):
        return checked(lambda: manager.batches.retry(id))

    @app.get("/api/tasks/{id}/logs")
    def task_logs(id: str):
        return checked(
            lambda: {
                name: tail(
                    manager.config.artifacts
                    / manager.store.get(id)["id"]
                    / (name + ".log")
                )
                for name in ("entrypoint", "stdout", "stderr")
            }
        )

    @app.post("/api/diagrams")
    def bulk():
        successful = {
            t["candidate"]
            for t in manager.store.tasks()
            if t["kind"] == "diagram"
            and t["status"] == "succeeded"
            and manager.store.artifact(t["id"] + "-diagram.png")
            and Path(manager.store.artifact(t["id"] + "-diagram.png")).exists()
        }
        return [
            manager.queue("diagram", c)
            for c in manager.candidates
            if c != "tfa" and c not in successful
        ]

    @app.post("/api/tasks/{id}/stop")
    def stop(id: str):
        return checked(lambda: manager.cancel_task(id))

    @app.get("/api/artifacts/{id}")
    def artifact(id: str):
        path = manager.store.artifact(id)
        if not path or not Path(path).is_file():
            raise HTTPException(404)
        return FileResponse(path)

    static = ROOT / "web/frontend/dist"
    if static.exists():
        app.mount("/", StaticFiles(directory=static, html=True), name="frontend")
    return app


def checked(fn):
    try:
        return fn()
    except NotImplementedError as error:
        raise HTTPException(501, str(error)) from error
    except (ValueError, OSError, StopIteration, subprocess.SubprocessError) as error:
        raise HTTPException(400, str(error)) from error
