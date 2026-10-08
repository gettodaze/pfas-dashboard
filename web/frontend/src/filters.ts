import { ramFields } from "./candidateResults";
import { Candidate } from "./data";

export const filterFields: Record<string, string> = {
  ...ramFields,
  cluster: "Cluster ID",
  n_points: "Points",
  MolecularWeight: "Avg MW",
  XLogP: "Avg XLogP",
  ExactMass: "Avg exact mass",
  Charge: "Avg charge",
  TPSA: "Avg TPSA",
  HBondDonorCount: "Avg H-bond donors",
  HBondAcceptorCount: "Avg H-bond acceptors",
  RotatableBondCount: "Avg rotatable bonds",
  medoid_CID: "Representative CID",
};
export type Filter = { field: string; operator: string; value: string };
export function matchesFilters(candidate: Candidate, filters: Filter[]) {
  return filters.every(({ field, operator, value }) => {
    const raw = candidate.fields[field];
    const actual = Number(raw),
      expected = Number(value);
    if (
      !raw?.trim() ||
      !value.trim() ||
      !Number.isFinite(actual) ||
      !Number.isFinite(expected)
    )
      return false;
    switch (operator) {
      case "<":
        return actual < expected;
      case "<=":
        return actual <= expected;
      case ">":
        return actual > expected;
      case ">=":
        return actual >= expected;
      case "=":
        return actual === expected;
      case "!=":
        return actual !== expected;
      default:
        return false;
    }
  });
}
export function describeFilters(filters: Filter[]) {
  return filters
    .map((f) => `${filterFields[f.field]} ${f.operator} ${f.value}`)
    .join(" AND ");
}

// IDs are exact; ranges are inclusive. Check intervals without expanding large ranges.
export function parseClusterIDs(query: string): {
  ranges: [number, number][];
  error?: string;
} {
  if (!query.trim()) return { ranges: [] };
  const ranges: [number, number][] = [];
  for (const part of query.split(",")) {
    const match = part.trim().match(/^(\d+)\s*(?:-\s*(\d+))?$/);
    if (!match)
      return {
        ranges: [],
        error:
          "Use cluster IDs or inclusive ranges separated by commas, e.g. 20-50 or 2-10, 15.",
      };
    const start = Number(match[1]),
      end = Number(match[2] ?? match[1]);
    if (
      !Number.isSafeInteger(start) ||
      !Number.isSafeInteger(end) ||
      start > end
    ) {
      return {
        ranges: [],
        error:
          "Ranges must use nonnegative integer IDs with the smaller ID first, e.g. 20-50.",
      };
    }
    ranges.push([start, end]);
  }
  return { ranges };
}
export function matchesClusterIDs(id: string, ranges: [number, number][]) {
  return (
    !ranges.length ||
    ranges.some(([start, end]) => Number(id) >= start && Number(id) <= end)
  );
}

const numericFields = new Set([
  ...Object.keys(ramFields),
  "cluster",
  "n_points",
  "medoid_CID",
  "MolecularWeight",
  "ExactMass",
  "Charge",
  "XLogP",
  "TPSA",
  "HBondDonorCount",
  "HBondAcceptorCount",
  "RotatableBondCount",
]);

export function sortCandidates(
  candidates: Candidate[],
  field: string,
  direction: string,
) {
  if (!field) return candidates;
  return [...candidates].sort((a, b) => {
    const left = a.fields[field]?.trim(),
      right = b.fields[field]?.trim();
    const numeric = numericFields.has(field);
    const missingLeft = !left || (numeric && !Number.isFinite(Number(left)));
    const missingRight = !right || (numeric && !Number.isFinite(Number(right)));
    if (missingLeft || missingRight)
      return Number(missingLeft) - Number(missingRight);
    const comparison = numeric
      ? Number(left) - Number(right)
      : left!.localeCompare(right!);
    return direction === "desc" ? -comparison : comparison;
  });
}
