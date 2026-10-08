export type Candidate = {
  task_summary?: {
    prepared?: Task;
    latestPreparation?: Task;
    ram: Record<string, Task>;
  };
  id: string;
  cid: string;
  smiles: string;
  fields: Record<string, string>;
};
export type Geometry = {
  atoms?: { element: string; position: number[] }[];
  units?: string;
  input_hash?: string;
  error?: string;
};
export type InputVersion = {
  input_id: string;
  candidate: string;
  system: string;
  label: string;
};
export type RAMValue = { value: string; unit: string; bytes: number };
export type Task = {
  version?: string;
  ram_estimate?: { per_process: RAMValue | null; total: RAMValue | null };
  runtime?: string;
  resources?: Resources;
  batch_id?: string;
  position?: number;
  usage?: {
    current_memory_bytes: number;
    peak_memory_bytes: number;
    measurement: string;
  };
  geometries?: Record<string, Geometry>;
  id: string;
  kind: string;
  candidate: string;
  system: string;
  processes: number;
  status: string;
  stop_requested?: boolean;
  created: string;
  started?: string;
  ended?: string;
  elapsed_seconds?: number;
  exit_code?: number;
  error?: string;
  command?: string[];
  artifacts: Record<string, string>;
  stdout_tail?: string;
  stderr_tail?: string;
  evidence?: {
    energy_ry: number | null;
    confirmed: boolean;
    provisional: boolean;
    job_done: boolean;
    scf_converged: boolean;
    relaxation_completed: boolean;
  };
};
export type Data = {
  input_versions?: InputVersion[];
  mode: "live" | "snapshot";
  queue?: { settings: QueueSettings; runtimes: RuntimeInfo };
  timestamp?: string;
  candidates: Candidate[];
  reference: Candidate;
  tasks: Task[];
};
export type Action = {
  input_id?: string;
  preview_id?: string;
  expected_pseudos?: Record<string, string>;
  kind: string;
  candidate: string;
  system?: string;
  processes?: number;
  target?: string;
  timeout?: number;
  expected_hash?: string;
  runtime?: string;
  memory_gib?: number;
};
export type Preview = {
  command: string[];
  input: string;
  input_hash: string;
  preview_id: string;
  pseudopotentials: Record<string, string>;
  geometry: Geometry;
};
declare const __DATA_MODE__: Data["mode"];

export async function readLive(): Promise<Data> {
  const response = await fetch("/api/data");
  if (!response.ok) throw new Error("Cannot load dashboard data");
  return response.json();
}

export async function readSnapshot(): Promise<Data> {
  const response = await fetch(new URL("snapshot/data.json", document.baseURI));
  if (!response.ok) throw new Error("Cannot load exported snapshot");
  return response.json();
}

export async function read(): Promise<Data> {
  return __DATA_MODE__ === "snapshot" ? readSnapshot() : readLive();
}
export async function post<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch("/api/" + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || response.statusText);
  }
  return response.json();
}

export type Resources = {
  cpus: number;
  memory_bytes: number;
  timeout?: number;
  hard_memory_limit: boolean;
};
export type RuntimeInfo = Record<
  "native" | "apptainer",
  {
    available: boolean;
    parallel: boolean;
    hard_memory_limit: boolean;
    message?: string;
  }
>;
export type QueueSettings = {
  paused: boolean;
  concurrency: number;
  memory_bytes: number;
  cpus: number;
};
export type BatchRequest = {
  candidates: string[];
  kind: string;
  system: string;
  runtime: string;
  processes: number;
  memory_gib: number;
  timeout?: number;
  retry?: boolean;
  rerun_completed?: boolean;
};
export type BatchEntry = {
  source_hash?: string;
  preview_id?: string;
  candidate: string;
  status: string;
  reason?: string;
  task_id?: string;
  input?: string;
  input_hash?: string;
  pseudopotentials?: Record<string, string>;
  command?: string[];
};
export type BatchPreview = {
  entries: BatchEntry[];
  resources: Resources;
  image_hash: string | null;
  runtime: string;
};
export type Batch = BatchRequest & {
  id: string;
  created: string;
  resources: Resources;
  entries: BatchEntry[];
  task_ids: string[];
};
export type QueueData = {
  settings: QueueSettings;
  runtimes: RuntimeInfo;
  batches: Batch[];
  tasks: Task[];
  running_count: number;
};
