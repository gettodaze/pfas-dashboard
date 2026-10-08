import { useEffect, useState } from "react";
import { post, BatchRequest, BatchPreview, RuntimeInfo } from "./data";

export function BatchControls({
  ids,
  runtimes,
  onQueued,
  initial,
}: {
  ids: string[];
  runtimes?: RuntimeInfo;
  onQueued: () => void;
  initial?: BatchRequest;
}) {
  const [kind, setKind] = useState(initial?.kind || "prepare");
  const [system, setSystem] = useState(initial?.system || "candidate");
  const [runtime, setRuntime] = useState(
    initial?.runtime ||
      (runtimes?.apptainer.available ? "apptainer" : "native"),
  );
  const [rerunCompleted, setRerunCompleted] = useState(
    initial?.rerun_completed || false,
  );
  const [processes, setProcesses] = useState(String(initial?.processes || 1));
  const [memory, setMemory] = useState(String(initial?.memory_gib || 4));
  const [timeout, setTimeout] = useState(
    initial?.timeout ? String(initial.timeout) : "",
  );
  const [preview, setPreview] = useState<BatchPreview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [operation, setOperation] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [page, setPage] = useState(0);
  useEffect(() => {
    if (!busy) return;
    setElapsed(0);
    const started = Date.now();
    const timer = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [busy]);
  const [reviewKey, setReviewKey] = useState("");
  const [submissionID, setSubmissionID] = useState("");
  const request: BatchRequest = {
    candidates: ids,
    kind,
    system,
    runtime,
    processes: kind === "qe" ? Number(processes) : 1,
    memory_gib: Number(memory),
    timeout: timeout ? Number(timeout) : undefined,
    retry: initial?.retry || false,
    rerun_completed: kind === "estimate_ram" && rerunCompleted,
  };
  const key = JSON.stringify(request);
  const valid =
    Number(memory) > 0 &&
    (kind !== "qe" ||
      (Number.isInteger(Number(processes)) && Number(processes) >= 1)) &&
    (!timeout || Number(timeout) > 0);
  const current = key === reviewKey ? preview : null;
  async function review() {
    setOperation(`Reviewing ${ids.length} selected jobs`);
    setBusy(true);
    setError("");
    try {
      setPreview(await post<BatchPreview>("batches/preview", request));
      setPage(0);
      setReviewKey(key);
      setSubmissionID(crypto.randomUUID());
    } catch (e) {
      setError(String(e));
      setPreview(null);
    } finally {
      setBusy(false);
    }
  }
  async function submit() {
    if (!current) return;
    setOperation(`Queueing ${counts?.eligible || 0} jobs`);
    setBusy(true);
    setError("");
    try {
      await post("batches", {
        ...request,
        id: submissionID,
        expected: current.entries.map(
          ({
            candidate,
            status,
            input_hash,
            source_hash,
            preview_id,
            pseudopotentials,
          }) => ({
            candidate,
            status,
            input_hash,
            source_hash,
            preview_id,
            pseudopotentials,
          }),
        ),
        image_hash: current.image_hash,
      });
      setPreview(null);
      onQueued();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  const counts = current?.entries.reduce<Record<string, number>>((all, e) => {
    all[e.status] = (all[e.status] || 0) + 1;
    return all;
  }, {});
  return (
    <section className="batch-controls">
      <h3>
        {initial ? "Retry unsuccessful jobs" : "Actions for selected molecules"}
      </h3>
      <p>
        {ids.length} selected · Jobs start in the displayed order. Each molecule
        has its own attempt.
      </p>
      <div className="controls">
        <label>
          Batch action{" "}
          <select
            aria-label="Batch action"
            value={`${kind}/${system}`}
            onChange={(e) => {
              const [k, s] = e.target.value.split("/");
              setKind(k);
              setSystem(s);
            }}
          >
            <option value="diagram/candidate">Generate missing diagrams</option>
            <option value="prepare/candidate">Prepare missing inputs</option>
            <option value="qe/candidate">Run QE · isolated candidate</option>
            <option value="qe/complex">Run QE · TFA complex</option>
            <option value="estimate_ram/candidate">
              Estimate RAM · isolated candidate
            </option>
            <option value="estimate_ram/complex">
              Estimate RAM · TFA complex
            </option>
          </select>
        </label>
        <label>
          Execution runtime{" "}
          <select
            aria-label="Execution runtime"
            value={runtime}
            onChange={(e) => setRuntime(e.target.value)}
          >
            <option value="native">Native · serial, no hard memory cap</option>
            <option value="apptainer" disabled={!runtimes?.apptainer.available}>
              Apptainer · hard memory cap
            </option>
          </select>
        </label>
        {runtime === "apptainer" && (
          <label>
            Memory per job (GiB){" "}
            <input
              aria-label="Memory per job (GiB)"
              type="number"
              min="0.125"
              step="0.125"
              value={memory}
              onChange={(e) => setMemory(e.target.value)}
            />
          </label>
        )}
        {kind === "qe" && (
          <label>
            Processes per job{" "}
            <input
              aria-label="Processes per job"
              type="number"
              min="1"
              step="1"
              value={processes}
              onChange={(e) => setProcesses(e.target.value)}
            />
          </label>
        )}
        <label>
          Timeout (seconds){" "}
          <input
            aria-label="Batch timeout (seconds)"
            type="number"
            min="1"
            value={timeout}
            placeholder={
              kind === "prepare"
                ? "900"
                : kind === "diagram"
                  ? "60"
                  : kind === "estimate_ram"
                    ? "120"
                    : "No timeout"
            }
            onChange={(e) => setTimeout(e.target.value)}
          />
        </label>
        {kind === "estimate_ram" && (
          <label className="rerun-option">
            <input
              type="checkbox"
              checked={rerunCompleted}
              onChange={(e) => setRerunCompleted(e.target.checked)}
            />
            Rerun existing estimates
          </label>
        )}
        <button disabled={busy || !ids.length || !valid} onClick={review}>
          Preview selected jobs
        </button>
      </div>
      {kind === "estimate_ram" && (
        <p>
          Existing successful estimates are skipped unless you select Rerun
          existing estimates. Results appear in the queue and task history.
        </p>
      )}
      {runtime === "native" && (
        <p>
          Native jobs run one at a time. Their memory is not isolated from WSL;
          use Apptainer for hard limits.
        </p>
      )}
      {!runtimes?.apptainer.available && (
        <p className="filter-help">
          For container execution, install Apptainer, build the chemistry image,
          and set PFAS_APPTAINER_IMAGE. See the dashboard documentation.
        </p>
      )}
      {busy && (
        <p role="status">
          {operation} · {elapsed}s elapsed…
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {current && (
        <div>
          <p role="status">
            {counts?.eligible || 0} eligible · {counts?.completed || 0}{" "}
            completed · {counts?.active || 0} already active ·{" "}
            {counts?.unavailable || 0} unavailable
          </p>
          <p>
            {runtime} · {current.resources.cpus} CPU(s) per job ·{" "}
            {current.resources.hard_memory_limit
              ? `${(current.resources.memory_bytes / 1024 ** 3).toFixed(2)} GiB cap`
              : "No hard memory cap"}{" "}
            ·{" "}
            {current.resources.timeout
              ? `${current.resources.timeout}s timeout`
              : "No timeout"}
          </p>
          <p>
            Showing {page * 50 + 1}–
            {Math.min((page + 1) * 50, current.entries.length)} of{" "}
            {current.entries.length} jobs
          </p>
          <button disabled={page === 0} onClick={() => setPage(page - 1)}>
            Previous jobs
          </button>
          <button
            disabled={(page + 1) * 50 >= current.entries.length}
            onClick={() => setPage(page + 1)}
          >
            Next jobs
          </button>
          <ol className="batch-preview" start={page * 50 + 1}>
            {current.entries.slice(page * 50, (page + 1) * 50).map((entry) => (
              <li key={entry.candidate}>
                <a href={`#candidate/${entry.candidate}`}>
                  Cluster {entry.candidate}
                </a>{" "}
                · {entry.status}
                {entry.reason && ` · ${entry.reason}`}
                {entry.input && (
                  <JobInput input={entry.input} command={entry.command} />
                )}
              </li>
            ))}
          </ol>
          <button
            disabled={busy || !counts?.eligible || !valid}
            onClick={submit}
          >
            Queue {counts?.eligible || 0} jobs
          </button>
        </div>
      )}
    </section>
  );
}

function JobInput({ input, command }: { input: string; command?: string[] }) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>Command and exact input</summary>
      {open && (
        <>
          <code>{command?.join(" ")}</code>
          <pre>{input}</pre>
        </>
      )}
    </details>
  );
}
