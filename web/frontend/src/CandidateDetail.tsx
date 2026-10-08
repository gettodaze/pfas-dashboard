import { useState } from "react";
import { Candidate, Task, Action, RuntimeInfo, InputVersion } from "./data";
import { PubChemLink } from "./PubChemLink";
import { TaskHistory } from "./TaskHistory";
import { GeometryViewer } from "./GeometryViewer";
import { RunControls } from "./RunControls";
export function CandidateDetail({
  candidate,
  tasks,
  live,
  action,
  runtimes,
  versions,
}: {
  runtimes?: RuntimeInfo;
  versions?: InputVersion[];
  candidate: Candidate;
  tasks: Task[];
  live: boolean;
  action: (path: string, body?: Action) => Promise<void>;
}) {
  const [input, setInput] = useState(""),
    [error, setError] = useState("");
  const [runtime, setRuntime] = useState(
    runtimes?.apptainer.available ? "apptainer" : "native",
  );
  const [memory, setMemory] = useState(4);
  const latestDiagram = tasks.find((t) => t.kind === "diagram");
  const latestPreparation = tasks.find((t) => t.kind === "prepare");
  const diagram = tasks.find(
    (t) =>
      t.kind === "diagram" &&
      t.status === "succeeded" &&
      t.artifacts["diagram.png"],
  );
  const prepared = tasks.find(
    (t) => t.kind === "prepare" && t.status === "succeeded",
  );
  return (
    <>
      <h2>
        {candidate.id === "tfa"
          ? "Shared neutral TFA reference"
          : `Cluster ${candidate.id}`}
      </h2>
      <p className="representative-summary">
        Representative molecule · PubChem CID{" "}
        <PubChemLink cid={candidate.cid} />
      </p>
      <pre>{candidate.smiles}</pre>
      {diagram ? (
        <img
          className="diagram"
          src={diagram.artifacts["diagram.png"]}
          alt={`Representative molecule for cluster ${candidate.id}`}
        />
      ) : (
        <div className="placeholder">Diagram has not been generated.</div>
      )}
      {live && (
        <div className="controls">
          <label>
            Preparation/diagram runtime{" "}
            <select
              value={runtime}
              onChange={(e) => setRuntime(e.target.value)}
            >
              <option value="native">
                Native · serial, no hard memory cap
              </option>
              <option
                disabled={!runtimes?.apptainer.available}
                value="apptainer"
              >
                Apptainer · hard memory cap
              </option>
            </select>
          </label>
          {runtime === "apptainer" && (
            <label>
              Memory per job (GiB){" "}
              <input
                type="number"
                min="0.125"
                step="0.125"
                value={memory}
                onChange={(e) => setMemory(Number(e.target.value))}
              />
            </label>
          )}
          <button
            onClick={() =>
              action("tasks", {
                kind: "diagram",
                candidate: candidate.id,
                runtime,
                memory_gib: memory,
              })
            }
          >
            Generate diagram
          </button>
          <button
            onClick={() =>
              action("tasks", {
                kind: "prepare",
                candidate: candidate.id,
                runtime,
                memory_gib: memory,
              })
            }
          >
            Prepare inputs
          </button>
        </div>
      )}
      {live &&
        [latestDiagram, latestPreparation]
          .filter((t): t is Task => Boolean(t))
          .map((t) => (
            <div
              key={t.id}
              role="status"
              className={t.status === "failed" ? "error" : "banner"}
            >
              <strong>
                {t.kind === "diagram" ? "Diagram" : "Preparation"}: {t.status}
              </strong>
              {["queued", "running"].includes(t.status) && (
                <span> · Use Refresh to update progress.</span>
              )}
              {["failed", "interrupted", "canceled"].includes(t.status) && (
                <pre>
                  {t.error ||
                    t.stderr_tail ||
                    "See task history below for details."}
                </pre>
              )}
            </div>
          ))}
      {candidate.id === "tfa" ? (
        <>
          <h3>Reference identity</h3>
          <dl>
            {Object.entries(candidate.fields).map(([k, v]) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        </>
      ) : (
        <>
          <section className="cluster-summary">
            <h3>Cluster information</h3>
            <p>Descriptors are averages across the cluster.</p>
            <dl>
              {Object.entries(candidate.fields)
                .filter(([k]) => !k.startsWith("medoid_"))
                .map(([k, v]) => (
                  <div key={k}>
                    <dt>{k}</dt>
                    <dd>{v}</dd>
                  </div>
                ))}
            </dl>
          </section>
          <section className="representative-summary">
            <h3>Representative molecule</h3>
            <dl>
              <div>
                <dt>CID</dt>
                <dd>
                  <PubChemLink cid={candidate.cid} />
                </dd>
              </div>
              <div>
                <dt>SMILES</dt>
                <dd>{candidate.smiles}</dd>
              </div>
            </dl>
          </section>
        </>
      )}
      {prepared?.geometries && (
        <GeometryViewer
          key={prepared.id}
          geometries={
            candidate.id === "tfa"
              ? Object.fromEntries(
                  Object.entries(prepared.geometries).filter(
                    ([name]) => name === "tfa",
                  ),
                )
              : prepared.geometries
          }
        />
      )}
      {prepared && (
        <section>
          <h3>Prepared inputs</h3>
          {Object.entries(prepared.artifacts)
            .filter(
              ([n]) =>
                n.endsWith(".in") && (candidate.id !== "tfa" || n === "tfa.in"),
            )
            .map(([name, url]) => (
              <div key={name}>
                <a href={url} download>
                  {name}
                </a>{" "}
                <button
                  onClick={async () => {
                    try {
                      const r = await fetch(url);
                      if (!r.ok) throw Error("Input unavailable");
                      setInput(await r.text());
                    } catch (e) {
                      setError(String(e));
                    }
                  }}
                >
                  View input
                </button>
              </div>
            ))}
          {error && <p className="error">{error}</p>}
          {input && <pre>{input}</pre>}
        </section>
      )}
      {live &&
        (candidate.id === "tfa" ? ["tfa"] : ["candidate", "complex"]).map(
          (system) => (
            <RunControls
              key={candidate.id + system}
              candidate={candidate.id}
              system={system}
              runtimes={runtimes}
              versions={versions}
              action={action}
            />
          ),
        )}
      <TaskHistory tasks={tasks} live={live} action={action} />
    </>
  );
}
