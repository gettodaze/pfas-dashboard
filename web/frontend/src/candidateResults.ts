import type { Candidate, Task } from "./data";

export const ramFields: Record<string, string> = {
  candidate_ram_per_process_gib: "Candidate Est RAM (GB)",
  complex_ram_per_process_gib: "Complex Est RAM (GB)",
};

export function withTaskFields(
  candidates: Candidate[],
  tasks: Task[],
): Candidate[] {
  const summaries = new Map<string, NonNullable<Candidate["task_summary"]>>();
  // Do not depend on API or snapshot task ordering. A failed retry never erases a success.
  const newest = [...tasks].sort((a, b) => b.created.localeCompare(a.created));
  for (const task of newest) {
    const summary = summaries.get(task.candidate) || { ram: {} };
    if (task.kind === "prepare") {
      summary.latestPreparation ||= task;
      if (task.status === "succeeded") summary.prepared ||= task;
    }
    if (
      task.kind === "estimate_ram" &&
      task.processes === 1 &&
      task.status === "succeeded" &&
      ["candidate", "complex"].includes(task.system) &&
      task.ram_estimate?.per_process &&
      Number.isFinite(task.ram_estimate.per_process.bytes) &&
      task.ram_estimate.per_process.bytes >= 0
    )
      summary.ram[task.system] ||= task;
    summaries.set(task.candidate, summary);
  }
  return candidates.map((candidate) => {
    const summary = summaries.get(candidate.id) || { ram: {} };
    const fields = { ...candidate.fields };
    for (const system of ["candidate", "complex"]) {
      const estimate = summary.ram[system]?.ram_estimate;
      const value = estimate?.per_process;
      fields[`${system}_ram_per_process_gib`] =
        value && Number.isFinite(value.bytes) && value.bytes >= 0
          ? String(value.bytes / 1024 ** 3)
          : "";
      delete fields[`${system}_ram_total_gib`];
    }
    return { ...candidate, fields, task_summary: summary };
  });
}

export function preparationLabel(candidate: Candidate): string {
  if (candidate.task_summary?.prepared) return "Prepared";
  const latest = candidate.task_summary?.latestPreparation;
  if (!latest) return "Not run";
  return (
    (
      {
        queued: "Queued",
        running: "Running",
        failed: "Failed",
        canceled: "Canceled",
        interrupted: "Interrupted",
      } as Record<string, string>
    )[latest.status] || latest.status
  );
}

export function matchesPreparation(
  candidate: Candidate,
  selection: string,
): boolean {
  return (
    selection === "all" ||
    Boolean(candidate.task_summary?.prepared) === (selection === "prepared")
  );
}

export function ramDisplay(candidate: Candidate, system: string): string {
  const raw = candidate.fields[`${system}_ram_per_process_gib`]?.trim();
  return raw && Number.isFinite(Number(raw))
    ? `${Number(raw).toFixed(0)} GB`
    : "Unavailable";
}

export function numericValue(raw: string | undefined): number | null {
  return raw?.trim() && Number.isFinite(Number(raw)) ? Number(raw) : null;
}

// AG Grid reverses comparator results for descending sorts. Cancel that reversal
// only for missing values, so they stay last in either direction.
export function compareGridNumbers(
  left: number | null,
  right: number | null,
  descending: boolean,
): number {
  const missingLeft = left === null || !Number.isFinite(left);
  const missingRight = right === null || !Number.isFinite(right);
  if (missingLeft || missingRight)
    return (Number(missingLeft) - Number(missingRight)) * (descending ? -1 : 1);
  return left! - right!;
}
