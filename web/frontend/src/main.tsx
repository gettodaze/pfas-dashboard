import React, { useState, useEffect, useMemo } from "react";
import { createRoot } from "react-dom/client";
import { read, post, Data, Action, Task } from "./data";
import { withTaskFields } from "./candidateResults";
import { AdvancedSearch } from "./AdvancedSearch";
import { Filter } from "./filters";
import { CandidateBrowser } from "./CandidateBrowser";
import { CandidateDetail } from "./CandidateDetail";
import { QueueView } from "./QueueView";
import { TaskHistory } from "./TaskHistory";
import "./style.css";
function App() {
  const [data, setData] = useState<Data | null>(null),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [hash, setHash] = useState(location.hash.slice(1)),
    [busy, setBusy] = useState(false),
    [filters, setFilters] = useState<Filter[]>([]);
  const refresh = async () => {
    try {
      setData(await read());
      setError("");
    } catch (e) {
      setError(String(e));
    }
  };
  useEffect(() => {
    void refresh();
    const update = () => setHash(location.hash.slice(1));
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  const action = async (path: string, body?: Action) => {
    setBusy(true);
    setNotice("");
    try {
      const result = await post<Task | Task[] | null>(path, body);
      if (Array.isArray(result)) {
        setNotice(
          `${result.length} diagram tasks queued. Open All tasks to view progress; use Refresh to update.`,
        );
      } else if (result?.kind) {
        setNotice(
          `${result.kind === "diagram" ? "Diagram" : result.kind === "prepare" ? "Preparation" : "QE"} task ${result.status}. Use Refresh to update progress.`,
        );
      } else {
        setNotice("Stop requested. Use Refresh to update task status.");
      }
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const browsingData = useMemo(
    () =>
      data
        ? { ...data, candidates: withTaskFields(data.candidates, data.tasks) }
        : null,
    [data],
  );
  const candidate =
    data &&
    (hash === "tfa"
      ? data.reference
      : data.candidates.find((c) => `candidate/${c.id}` === hash));
  return (
    <>
      <header>
        <a href="#">PFAS / Candidate dashboard</a>
        <nav>
          <a href="#">Candidates</a>
          <a href="#advanced-search">Advanced search</a>
          <a href="#tfa">TFA reference</a>
          <a href="#tasks">All tasks</a>
          {data?.mode === "live" && <a href="#queue">Job queue</a>}
          <button disabled={busy} onClick={refresh}>
            Refresh
          </button>
        </nav>
      </header>
      <main
        aria-busy={busy}
        className={
          !candidate && !["tasks", "advanced-search", "queue"].includes(hash)
            ? "candidate-page"
            : undefined
        }
      >
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="banner">
            {notice}
          </p>
        )}
        {!data ? (
          <p>Loading candidates…</p>
        ) : (
          <>
            <p className="banner">
              {data.mode === "live"
                ? "Local live dashboard · Native jobs serial; container jobs follow queue limits"
                : `Read-only snapshot · Exported ${data.timestamp}`}
            </p>
            {data.mode === "snapshot" && (
              <p>
                To generate diagrams, prepare inputs, or run QE locally:{" "}
                <code>
                  uv run --no-default-groups --group web --group preparation
                  python -m dashboard
                </code>{" "}
                from the repository. See{" "}
                <a href="https://github.com/Human-Augment-Analytics/pfas-environment-cleanup">
                  repository documentation
                </a>
                .
              </p>
            )}
            {candidate ? (
              <CandidateDetail
                key={candidate.id}
                candidate={candidate}
                tasks={data.tasks.filter(
                  (t) =>
                    t.candidate === candidate.id ||
                    (candidate.id === "tfa" &&
                      t.kind === "prepare" &&
                      t.status === "succeeded" &&
                      Boolean(t.artifacts["tfa.in"])),
                )}
                live={data.mode === "live"}
                runtimes={data.queue?.runtimes}
                versions={data.input_versions}
                action={action}
              />
            ) : hash === "queue" && data.mode === "live" ? (
              <QueueView />
            ) : hash === "tasks" ? (
              <TaskHistory
                tasks={data.tasks}
                live={data.mode === "live"}
                action={action}
              />
            ) : hash === "advanced-search" ? (
              <AdvancedSearch
                candidates={browsingData!.candidates}
                filters={filters}
                apply={(next) => {
                  setFilters(next);
                  location.hash = "";
                }}
              />
            ) : null}
            <div
              hidden={
                Boolean(candidate) ||
                hash === "tasks" ||
                (hash === "queue" && data.mode === "live") ||
                hash === "advanced-search"
              }
            >
              <CandidateBrowser
                data={browsingData!}
                busy={busy}
                action={action}
                filters={filters}
                clearFilters={() => setFilters([])}
                refresh={refresh}
              />
            </div>
          </>
        )}
      </main>
    </>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
