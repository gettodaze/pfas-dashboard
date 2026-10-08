import { useState } from "react";
import { Candidate, Task, Action, RuntimeInfo, InputVersion } from "./data";
import { PubChemLink } from "./PubChemLink";
import { TaskHistory } from "./TaskHistory";
import { GeometryViewer } from "./GeometryViewer";
import { RunControls } from "./RunControls";
import { InputDependencies } from "./InputDependencies";
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
  const [memory, setMemory] = useState("4");
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
    <div className="candidate-detail">
      <h2>
        {candidate.id === "tfa"
          ? "Shared neutral TFA reference"
          : `Cluster ${candidate.id}`}
      </h2>
      <div className="candidate-layout">
        <div className="candidate-work-column">
          <section className="cluster-summary detail-section">
            <h3>{candidate.id === "tfa" ? "Reference identity" : "Cluster"}</h3>
            {candidate.id !== "tfa" && (
              <p>Descriptors are averages across the cluster.</p>
            )}
            <dl>
              {Object.entries(candidate.fields)
                .filter(([key]) => !key.startsWith("medoid_"))
                .map(([key, value]) => (
                  <div key={key}>
                    <dt>{key}</dt>
                    <dd>{value}</dd>
                  </div>
                ))}
            </dl>
          </section>
          <section className="run-task-section detail-section">
            <h3>Run Task</h3>
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
                      onChange={(e) => setMemory(e.target.value)}
                    />
                  </label>
                )}
                <button
                  disabled={Number(memory) <= 0}
                  onClick={() =>
                    action("tasks", {
                      kind: "diagram",
                      candidate: candidate.id,
                      runtime,
                      memory_gib: Number(memory),
                    })
                  }
                >
                  Generate diagram
                </button>
                <button
                  disabled={Number(memory) <= 0}
                  onClick={() =>
                    action("tasks", {
                      kind: "prepare",
                      candidate: candidate.id,
                      runtime,
                      memory_gib: Number(memory),
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
                      {t.kind === "diagram" ? "Diagram" : "Preparation"}:{" "}
                      {t.status}
                    </strong>
                    {["queued", "running"].includes(t.status) && (
                      <span> · Use Refresh to update progress.</span>
                    )}
                    {["failed", "interrupted", "canceled"].includes(
                      t.status,
                    ) && (
                      <pre>
                        {t.error ||
                          t.stderr_tail ||
                          "See task history below for details."}
                      </pre>
                    )}
                  </div>
                ))}
            {prepared && (
              <section>
                <h3>Prepared inputs and structures</h3>
                {Object.entries(prepared.artifacts)
                  .filter(
                    ([n]) =>
                      /\.(in|png|cif|mol|xyz)$/i.test(n) &&
                      (candidate.id !== "tfa" || n.startsWith("tfa.")),
                  )
                  .map(([name, url]) => (
                    <div key={name}>
                      <a href={url} download={name}>
                        {name}
                      </a>{" "}
                      {name.endsWith(".in") && (
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
                      )}
                      <InputDependencies
                        asset={prepared.input_assets?.[name]}
                      />
                    </div>
                  ))}
                {error && <p className="error">{error}</p>}
                {input && <pre>{input}</pre>}
              </section>
            )}
            {!live && (
              <section>
                <h3>External input versions</h3>
                {!versions?.some((v) => v.candidate === candidate.id) && (
                  <p>No external input versions available.</p>
                )}
                {versions
                  ?.filter((v) => v.candidate === candidate.id)
                  .map((version) => (
                    <article key={version.input_id}>
                      <p>
                        {version.system} · {version.label}
                      </p>
                      {Object.entries(version.artifacts || {}).map(
                        ([name, url]) => (
                          <div key={name}>
                            <a href={url} download={name}>
                              {name}
                            </a>
                            <InputDependencies
                              asset={version.input_assets?.[name]}
                            />
                          </div>
                        ),
                      )}
                    </article>
                  ))}
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
          </section>
        </div>
        <section className="representative-summary detail-section">
          <h3>Representative</h3>
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
          {diagram ? (
            <img
              className="diagram"
              src={diagram.artifacts["diagram.png"]}
              alt={`Representative molecule for cluster ${candidate.id}`}
            />
          ) : (
            <div className="placeholder">Diagram has not been generated.</div>
          )}
        </section>
      </div>
      <div className="task-history-section detail-section">
        <TaskHistory tasks={tasks} live={live} action={action} />
      </div>
    </div>
  );
}
