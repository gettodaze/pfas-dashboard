import { useState } from "react";
import { Action, post, Preview, RuntimeInfo, InputVersion } from "./data";
import { GeometryViewer } from "./GeometryViewer";
export function RunControls({
  candidate,
  system,
  action,
  runtimes,
  versions,
}: {
  runtimes?: RuntimeInfo;
  versions?: InputVersion[];
  candidate: string;
  system: string;
  action: (path: string, body?: Action) => Promise<void>;
}) {
  const [processes, setProcesses] = useState("1"),
    [target, setTarget] = useState("local"),
    [timeout, setTimeout] = useState("");
  const [runtime, setRuntime] = useState(
    runtimes?.apptainer.available ? "apptainer" : "native",
  );
  const [inputId, setInputId] = useState("");
  const [kind, setKind] = useState("qe");
  const [memory, setMemory] = useState("4");
  const [preview, setPreview] = useState<Preview | null>(null),
    [error, setError] = useState("");
  const body: Action = {
    kind,
    ...(inputId ? { input_id: inputId } : {}),
    runtime,
    memory_gib: Number(memory),
    candidate,
    system,
    processes: kind === "estimate_ram" ? 1 : Number(processes),
    target,
    ...(timeout ? { timeout: Number(timeout) } : {}),
  };
  const valid =
    Number(memory) > 0 &&
    (kind === "estimate_ram" ||
      (Number.isInteger(Number(processes)) && Number(processes) >= 1)) &&
    (!timeout || Number(timeout) > 0);
  const changed = () => {
    setPreview(null);
    setError("");
  };
  return (
    <article>
      <h3>Run QE · {system}</h3>
      <div className="controls">
        <label>
          Input version{" "}
          <select
            value={inputId}
            onChange={(e) => {
              setInputId(e.target.value);
              changed();
            }}
          >
            <option value="">Prepared default</option>
            {versions
              ?.filter(
                (v) =>
                  v.system === system &&
                  v.candidate === (system === "tfa" ? "tfa" : candidate),
              )
              .map((v) => (
                <option key={v.input_id} value={v.input_id}>
                  {v.label}
                </option>
              ))}
          </select>
        </label>
        <label>
          Task{" "}
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              changed();
            }}
          >
            <option value="qe">Run QE</option>
            <option value="estimate_ram">Estimate RAM</option>
          </select>
        </label>
        <label>
          Execution runtime{" "}
          <select
            value={runtime}
            onChange={(e) => {
              setRuntime(e.target.value);
              changed();
            }}
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
              type="number"
              min="0.125"
              step="0.125"
              value={memory}
              onChange={(e) => {
                setMemory(e.target.value);
                changed();
              }}
            />
          </label>
        )}
        {kind === "qe" && (
          <label>
            Processes{" "}
            <input
              type="number"
              min="1"
              step="1"
              value={processes}
              onChange={(e) => {
                setProcesses(e.target.value);
                changed();
              }}
            />
          </label>
        )}
        <label>
          Target{" "}
          <select
            value={target}
            onChange={(e) => {
              setTarget(e.target.value);
              changed();
            }}
          >
            <option value="local">Local</option>
            <option value="slurm">Slurm (not implemented)</option>
            <option value="slurm-array">Slurm array (not implemented)</option>
          </select>
        </label>
        <label>
          Timeout in seconds (
          {kind === "estimate_ram" ? "default 120" : "optional"}){" "}
          <input
            type="number"
            min="1"
            value={timeout}
            onChange={(e) => {
              setTimeout(e.target.value);
              changed();
            }}
          />
        </label>
        <button
          disabled={!valid}
          onClick={async () => {
            try {
              setPreview(await post<Preview>("preview", body));
              setError("");
            } catch (e) {
              setError(String(e));
            }
          }}
        >
          Preview command and input
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {preview && (
        <>
          <pre>{preview.command.join(" ")}</pre>
          <GeometryViewer
            key={preview.input_hash}
            geometries={{ [system]: preview.geometry }}
          />
          <details open>
            <summary>Exact input to be submitted</summary>
            <pre>{preview.input}</pre>
          </details>
          <button
            disabled={!valid}
            onClick={async () => {
              await action("tasks", {
                ...body,
                expected_hash: preview.input_hash,
                preview_id: preview.preview_id,
                expected_pseudos: preview.pseudopotentials,
              });
              setPreview(null);
            }}
          >
            {kind === "estimate_ram" ? "Estimate RAM" : "Run QE"}
          </button>
        </>
      )}
    </article>
  );
}
