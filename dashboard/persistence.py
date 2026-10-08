import json
import sqlite3
import threading
from datetime import UTC, datetime


def now():
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, path TEXT NOT NULL)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS task_action ON tasks (json_extract(data, '$.kind'), json_extract(data, '$.candidate'), json_extract(data, '$.system'), json_extract(data, '$.status'))"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS settings (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        self.db.commit()
        for task in self.tasks():
            if task["status"] == "running":
                self.update(
                    task["id"],
                    status="interrupted",
                    ended=now(),
                    error="Server restarted; retry explicitly",
                )

        # Recover a crash during batch acceptance, including tasks persisted before
        # their batch entry was saved. Accepted queued work remains eligible to run.
        recovered_tasks = self.tasks()
        for batch in self.batches():
            entries = {entry["candidate"]: entry for entry in batch["entries"]}
            task_ids = set(batch["task_ids"])
            for task in recovered_tasks:
                if task.get("batch_id") == batch["id"]:
                    task_ids.add(task["id"])
                    entries[task["candidate"]] = {
                        "candidate": task["candidate"],
                        "status": "queued",
                        "task_id": task["id"],
                        "reason": "",
                    }
            for candidate in batch["candidates"]:
                entries.setdefault(
                    candidate,
                    {
                        "candidate": candidate,
                        "status": "unavailable",
                        "reason": "Batch acceptance interrupted; review again",
                    },
                )
            batch["entries"] = [entries[id] for id in batch["candidates"]]
            batch["task_ids"] = [
                entries[id]["task_id"]
                for id in batch["candidates"]
                if entries[id].get("task_id") in task_ids
            ]
            self.put_batch(batch)

    def tasks(self):
        with self.lock:
            return [
                json.loads(row[0])
                for row in self.db.execute("SELECT data FROM tasks ORDER BY rowid DESC")
            ]

    def get(self, id):
        with self.lock:
            row = self.db.execute("SELECT data FROM tasks WHERE id=?", (id,)).fetchone()
        if row is None:
            raise ValueError("Unknown task")
        return json.loads(row[0])

    def prepared(self, candidate, system):
        # Resolve defaults without decoding the entire history for every queued job.
        sql = "SELECT data FROM tasks WHERE json_extract(data, '$.kind')='prepare' AND json_extract(data, '$.status')='succeeded'"
        parameters = ()
        if system != "tfa":
            sql += " AND json_extract(data, '$.candidate')=?"
            parameters = (candidate,)
        with self.lock:
            return [
                json.loads(row[0])
                for row in self.db.execute(sql + " ORDER BY rowid DESC", parameters)
            ]

    def active(self, kind, candidate, system):
        with self.lock:
            row = self.db.execute(
                "SELECT data FROM tasks WHERE json_extract(data, '$.kind')=? AND json_extract(data, '$.candidate')=? AND json_extract(data, '$.system')=? AND json_extract(data, '$.status') IN ('queued', 'running') LIMIT 1",
                (kind, candidate, system),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def next_queued(self):
        with self.lock:
            row = self.db.execute(
                "SELECT data FROM tasks WHERE json_extract(data, '$.status')='queued' ORDER BY rowid LIMIT 1"
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, task):
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO tasks VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (task["id"], json.dumps(task)),
            )
        return task

    def update(self, id, **values):
        with self.lock:
            return self.put({**self.get(id), **values})

    def register(self, id, path):
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO artifacts VALUES (?, ?)", (id, str(path))
            )
        return "/api/artifacts/" + id

    def artifact(self, id):
        with self.lock:
            row = self.db.execute(
                "SELECT path FROM artifacts WHERE id=?", (id,)
            ).fetchone()
        return row[0] if row else None

    def batches(self):
        with self.lock:
            return [
                json.loads(row[0])
                for row in self.db.execute(
                    "SELECT data FROM batches ORDER BY rowid DESC"
                )
            ]

    def batch(self, id):
        with self.lock:
            row = self.db.execute(
                "SELECT data FROM batches WHERE id=?", (id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put_batch(self, batch):
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO batches VALUES (?, ?)",
                (batch["id"], json.dumps(batch)),
            )
        return batch

    def settings(self, defaults):
        with self.lock:
            row = self.db.execute(
                "SELECT data FROM settings WHERE id='queue'"
            ).fetchone()
        return {**defaults, **(json.loads(row[0]) if row else {})}

    def set_settings(self, settings):
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO settings VALUES ('queue', ?)",
                (json.dumps(settings),),
            )
        return settings

    def queued(self):
        return [t for t in reversed(self.tasks()) if t["status"] == "queued"]
