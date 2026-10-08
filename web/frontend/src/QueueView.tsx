import { useEffect, useState } from "react";
import { Batch, BatchRequest, QueueData, post } from "./data";
import { BatchControls } from "./BatchControls";

export function QueueView() {
  const [data, setData] = useState<QueueData | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [retry, setRetry] = useState<BatchRequest | null>(null);
  const [logs, setLogs] = useState<Record<string, string> | null>(null);
  const [jobPage, setJobPage] = useState(0);
  const [batchPage, setBatchPage] = useState(0);
  const [concurrency, setConcurrency] = useState("1"),
    [memory, setMemory] = useState("8"),
    [cpus, setCPUs] = useState("4");
  async function load() {
    try {
      const r = await fetch("/api/queue");
      if (!r.ok) throw Error("Cannot load queue");
      const result: QueueData = await r.json();
      setData(result);
      return result;
    } catch (e) {
      setError(String(e));
    }
  }
  useEffect(() => {
    let alive = true;
    void load().then((result) => {
      if (result && alive) {
        setConcurrency(String(result.settings.concurrency));
        setMemory(String(result.settings.memory_bytes / 1024 ** 3));
        setCPUs(String(result.settings.cpus));
      }
    });
    const timer = setInterval(() => {
      void load();
    }, 2000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);
  async function mutate(path: string, body?: unknown, message = "") {
    try {
      setError("");
      setNotice("");
      setPending(path);
      const result = await post<{ canceled_count?: number }>(path, body);
      setNotice(
        path === "queue/clear"
          ? `Canceled ${result.canceled_count} queued jobs. Running jobs continue.`
          : message,
      );
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setPending(null);
    }
  }
  async function retryBatch(id: string) {
    try {
      const r = await fetch(`/api/batches/${id}/retry`);
      const result = await r.json();
      if (!r.ok) throw Error(result.detail);
      setRetry(result);
    } catch (e) {
      setError(String(e));
    }
  }
  if (!data) return <p>Loading queue… {error}</p>;
  const active = data.tasks.filter((t) =>
    ["running", "queued"].includes(t.status),
  );
  const reserved = data.tasks
    .filter((t) => t.status === "running" && t.runtime === "apptainer")
    .reduce((total, t) => total + (t.resources?.memory_bytes || 0), 0);
  const attempts = data.tasks.filter(
    (t) => t.batch_id || ["running", "queued"].includes(t.status),
  );
  const currentJobPage = Math.min(
    jobPage,
    Math.max(0, Math.ceil(attempts.length / 50) - 1),
  );
  const currentBatchPage = Math.min(
    batchPage,
    Math.max(0, Math.ceil(data.batches.length / 10) - 1),
  );
  return (
    <section>
      <h1>Job queue</h1>
      <p role="status">
        {data.settings.paused ? "Paused" : "Accepting jobs"} ·{" "}
        {data.running_count} running ·{" "}
        {active.filter((t) => t.status === "queued").length} queued ·{" "}
        {(reserved / 1024 ** 3).toFixed(2)} GiB reserved
      </p>
      <div className="controls">
        <button
          disabled={pending !== null}
          onClick={() =>
            mutate("queue/settings", { paused: !data.settings.paused })
          }
        >
          {data.settings.paused ? "Resume queue" : "Pause queue"}
        </button>
        <button
          disabled={
            pending !== null || !active.some((t) => t.status === "queued")
          }
          onClick={() => mutate("queue/clear")}
        >
          {pending === "queue/clear" ? "Clearing queue…" : "Clear queue"}
        </button>
        <label>
          Maximum container jobs{" "}
          <input
            disabled={!data.runtimes.apptainer.parallel}
            type="number"
            min="1"
            step="1"
            value={concurrency}
            onChange={(e) => setConcurrency(e.target.value)}
          />
        </label>
        <label>
          Total memory budget (GiB){" "}
          <input
            type="number"
            min="0.125"
            step="0.125"
            value={memory}
            onChange={(e) => setMemory(e.target.value)}
          />
        </label>
        <label>
          Total CPU budget{" "}
          <input
            type="number"
            min="1"
            step="1"
            value={cpus}
            onChange={(e) => setCPUs(e.target.value)}
          />
        </label>
        <button
          disabled={
            pending !== null ||
            Number(memory) <= 0 ||
            !Number.isInteger(Number(concurrency)) ||
            Number(concurrency) < 1 ||
            !Number.isInteger(Number(cpus)) ||
            Number(cpus) < 1
          }
          onClick={() =>
            mutate("queue/settings", {
              concurrency: Number(concurrency),
              cpus: Number(cpus),
              memory_bytes: Math.round(Number(memory) * 1024 ** 3),
            })
          }
        >
          Save queue limits
        </button>
      </div>
      <p>
        Native jobs stay serial. Parallel container jobs must fit both budgets.
        Pause lets running jobs finish. Clear queue cancels all waiting jobs and
        keeps job history.
      </p>
      {notice && <p role="status">{notice}</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {retry && (
        <BatchControls
          key={JSON.stringify(retry)}
          ids={retry.candidates}
          initial={retry}
          runtimes={data.runtimes}
          onQueued={() => {
            setRetry(null);
            void load();
          }}
        />
      )}
      <h2>Batches</h2>
      {!data.batches.length && (
        <p>
          No batches yet. Select molecules in the candidate view to create one.
        </p>
      )}
      <Pages
        count={data.batches.length}
        size={10}
        page={currentBatchPage}
        onPage={setBatchPage}
        label="batches"
      />
      {data.batches
        .slice(currentBatchPage * 10, (currentBatchPage + 1) * 10)
        .map((batch) => {
          const tasks = data.tasks.filter((t) => t.batch_id === batch.id);
          const counts = tasks.reduce<Record<string, number>>((all, t) => {
            const status =
              t.status === "running" && t.stop_requested
                ? "stopping"
                : t.status;
            all[status] = (all[status] || 0) + 1;
            return all;
          }, {});
          return (
            <article key={batch.id}>
              <strong>
                {batch.kind} · {batch.system} · {batch.runtime}
              </strong>
              <p>
                {batch.created} · {batch.candidates.length} selected
              </p>
              <p>
                {Object.entries(counts)
                  .map(([status, n]) => `${n} ${status}`)
                  .join(" · ")}{" "}
                · {batch.entries.filter((e) => e.status !== "queued").length}{" "}
                skipped/unavailable
              </p>
              <div className="controls">
                <button
                  disabled={
                    pending !== null ||
                    !tasks.some(
                      (t) =>
                        t.status === "queued" ||
                        (t.status === "running" && !t.stop_requested),
                    )
                  }
                  onClick={() =>
                    mutate(
                      `batches/${batch.id}/cancel`,
                      undefined,
                      "Batch cancellation requested. Waiting jobs are canceled; running jobs are stopping.",
                    )
                  }
                >
                  {pending === `batches/${batch.id}/cancel`
                    ? "Canceling batch…"
                    : "Cancel batch"}
                </button>
                <button onClick={() => retryBatch(batch.id)}>
                  Review unsuccessful jobs
                </button>
              </div>
              <BatchEntries batch={batch} tasks={tasks} />
            </article>
          );
        })}
      <h2>Job attempts</h2>
      <Pages
        count={attempts.length}
        size={50}
        page={currentJobPage}
        onPage={setJobPage}
        label="jobs"
      />
      {attempts
        .slice(currentJobPage * 50, (currentJobPage + 1) * 50)
        .map((t) => (
          <article key={t.id}>
            <a
              href={
                t.candidate === "tfa" ? "#tfa" : `#candidate/${t.candidate}`
              }
            >
              Cluster {t.candidate}
            </a>{" "}
            ·{" "}
            <strong>
              {t.kind} · {t.system} ·{" "}
              {t.status === "running" && t.stop_requested
                ? "Stopping…"
                : t.status}
            </strong>
            <p>
              {t.runtime || "native"} · {t.resources?.cpus || t.processes}{" "}
              CPU(s) ·{" "}
              {t.usage
                ? `${(t.usage.current_memory_bytes / 1024 ** 2).toFixed(1)} MiB current / ${(t.usage.peak_memory_bytes / 1024 ** 2).toFixed(1)} MiB peak (${t.usage.measurement})`
                : "Memory usage pending"}
            </p>
            {t.started && (
              <p>
                Started {t.started} ·{" "}
                {Math.max(
                  0,
                  ((t.ended ? Date.parse(t.ended) : Date.now()) -
                    Date.parse(t.started)) /
                    1000,
                ).toFixed(1)}{" "}
                seconds elapsed
              </p>
            )}
            {t.version && (
              <p>
                {t.version} · {t.processes} processes
              </p>
            )}
            {t.ram_estimate && (
              <p>
                Est RAM:{" "}
                {t.processes === 1 && t.ram_estimate.per_process
                  ? `${t.ram_estimate.per_process.value} ${t.ram_estimate.per_process.unit}`
                  : "unavailable"}
              </p>
            )}
            {t.evidence && (
              <p>
                QE completion{" "}
                {t.evidence.confirmed ? "confirmed" : "not confirmed"} · Energy{" "}
                {t.evidence.energy_ry ?? "unavailable"} Ry (
                {t.evidence.provisional ? "provisional" : "confirmed"})
              </p>
            )}
            {t.error && <p className="error">{t.error}</p>}
            <div className="controls">
              {["queued", "running"].includes(t.status) && (
                <button
                  disabled={pending !== null || t.stop_requested}
                  onClick={() => mutate(`tasks/${t.id}/stop`)}
                >
                  {t.stop_requested ? "Stopping…" : "Stop job"}
                </button>
              )}
              <button
                onClick={async () => {
                  try {
                    const r = await fetch(`/api/tasks/${t.id}/logs`);
                    if (!r.ok) throw Error("Logs unavailable");
                    setLogs(await r.json());
                  } catch (e) {
                    setError(String(e));
                  }
                }}
              >
                View log tail
              </button>
              {Object.entries(t.artifacts)
                .filter(([n]) => n.endsWith(".log"))
                .map(([name, url]) => (
                  <a key={name} href={url} download>
                    {name}
                  </a>
                ))}
            </div>
          </article>
        ))}
      {logs && (
        <section>
          <h2>Selected job logs</h2>
          <button onClick={() => setLogs(null)}>Close logs</button>
          {Object.entries(logs).map(([name, text]) => (
            <div key={name}>
              <h3>{name}</h3>
              <pre>{text || "No output yet."}</pre>
            </div>
          ))}
        </section>
      )}
    </section>
  );
}

function Pages({
  count,
  size,
  page,
  onPage,
  label,
}: {
  count: number;
  size: number;
  page: number;
  onPage: (page: number) => void;
  label: string;
}) {
  if (!count) return null;
  return (
    <nav aria-label={`${label} pages`}>
      <p>
        Showing {page * size + 1}–{Math.min((page + 1) * size, count)} of{" "}
        {count} {label}
      </p>
      <button disabled={page === 0} onClick={() => onPage(page - 1)}>
        Previous {label}
      </button>
      <button
        disabled={(page + 1) * size >= count}
        onClick={() => onPage(page + 1)}
      >
        Next {label}
      </button>
    </nav>
  );
}

function BatchEntries({
  batch,
  tasks,
}: {
  batch: Batch;
  tasks: QueueData["tasks"];
}) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(0);
  const statuses = new Map(tasks.map((t) => [t.id, t.status]));
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>Batch entries</summary>
      {open && (
        <>
          <Pages
            count={batch.entries.length}
            size={50}
            page={page}
            onPage={setPage}
            label="entries"
          />
          <ol start={page * 50 + 1}>
            {batch.entries.slice(page * 50, (page + 1) * 50).map((e) => (
              <li key={e.candidate}>
                Cluster {e.candidate} ·{" "}
                {statuses.get(e.task_id || "") || e.status}
                {e.reason && ` · ${e.reason}`}
              </li>
            ))}
          </ol>
        </>
      )}
    </details>
  );
}
