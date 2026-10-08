import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

// Exercise the source modules with the project's installed TypeScript compiler.
function sourceURL(name, replacements = {}) {
  let code = ts.transpileModule(
    readFileSync(new URL(`../src/${name}.ts`, import.meta.url), "utf8"),
    {
      compilerOptions: {
        module: ts.ModuleKind.ES2022,
        target: ts.ScriptTarget.ES2020,
      },
    },
  ).outputText;
  for (const [original, replacement] of Object.entries(replacements))
    code = code.replace(original, replacement);
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const resultsURL = sourceURL("candidateResults");
const {
  withTaskFields,
  preparationLabel,
  matchesPreparation,
  ramDisplay,
  compareGridNumbers,
  numericValue,
} = await import(resultsURL);
const { matchesFilters, sortCandidates } = await import(
  sourceURL("filters", { '"./candidateResults"': JSON.stringify(resultsURL) })
);
const candidate = (id = "500") => ({ id, fields: { MolecularWeight: "100" } });
const ram = (bytes) => ({
  value: String(bytes / 1024 ** 2),
  unit: "MB",
  bytes,
});
const attempt = (id, overrides = {}) => ({
  id,
  candidate: "500",
  kind: "estimate_ram",
  system: "candidate",
  status: "succeeded",
  created: `2026-10-07T00:00:0${id}Z`,
  artifacts: {},
  processes: 1,
  ram_estimate: { per_process: ram(1024 ** 3), total: null },
  ...overrides,
});

test("latest successful estimates remain separate by system and retain provenance", () => {
  const latest = attempt("3", {
    processes: 1,
    version: "candidate.big.in",
    ram_estimate: {
      per_process: ram(2 * 1024 ** 3),
      total: ram(5 * 1024 ** 3),
    },
  });
  const tasks = [
    attempt("1"),
    latest,
    attempt("2", {
      system: "complex",
      ram_estimate: { per_process: ram(6 * 1024 ** 3), total: null },
    }),
    attempt("4", {
      status: "failed",
      ram_estimate: { per_process: ram(8 * 1024 ** 3), total: null },
    }),
  ];
  const [result] = withTaskFields([candidate()], tasks);
  assert.equal(result.fields.candidate_ram_per_process_gib, "2");
  assert.equal(result.fields.candidate_ram_total_gib, undefined);
  assert.equal(result.fields.complex_ram_per_process_gib, "6");
  assert.equal(result.fields.complex_ram_total_gib, undefined);
  assert.equal(result.task_summary.ram.candidate, latest);
});

test("missing and unsuccessful estimates never become zero or enter numeric filters", () => {
  const rows = withTaskFields(
    [candidate(), candidate("501")],
    [attempt("1", { status: "running" }), attempt("2", { kind: "qe" })],
  );
  for (const row of rows) {
    assert.equal(row.fields.candidate_ram_per_process_gib, "");
    assert.equal(
      matchesFilters(row, [
        { field: "candidate_ram_per_process_gib", operator: "<=", value: "10" },
      ]),
      false,
    );
  }
});

test("Est RAM keeps single-process estimates when a newer multi-process run exists", () => {
  const [row] = withTaskFields(
    [candidate()],
    [
      attempt("1"),
      attempt("2", {
        processes: 4,
        ram_estimate: {
          per_process: ram(8 * 1024 ** 3),
          total: ram(10 * 1024 ** 3),
        },
      }),
    ],
  );
  assert.equal(row.fields.candidate_ram_per_process_gib, "1");
  assert.equal(row.fields.candidate_ram_total_gib, undefined);
});

test("RAM conditions use binary GiB and combine with descriptor filters", () => {
  const [row] = withTaskFields(
    [candidate()],
    [
      attempt("1", {
        ram_estimate: { per_process: ram(512 * 1024 ** 2), total: null },
      }),
    ],
  );
  assert.equal(row.fields.candidate_ram_per_process_gib, "0.5");
  assert.equal(
    matchesFilters(row, [
      { field: "candidate_ram_per_process_gib", operator: ">=", value: "0.5" },
      { field: "MolecularWeight", operator: "<", value: "200" },
    ]),
    true,
  );
  assert.equal(
    matchesFilters(row, [
      { field: "candidate_ram_total_gib", operator: "!=", value: "0" },
    ]),
    false,
  );
});

test("preparation success survives a failed retry and snapshot artifact omission", () => {
  const successful = attempt("1", { kind: "prepare", status: "succeeded" });
  const failed = attempt("2", { kind: "prepare", status: "failed" });
  const [row, absent] = withTaskFields(
    [candidate(), candidate("501")],
    [successful, failed],
  );
  assert.equal(preparationLabel(row), "Prepared");
  assert.equal(row.task_summary.latestPreparation, failed);
  assert.equal(matchesPreparation(row, "prepared"), true);
  assert.equal(matchesPreparation(row, "not_prepared"), false);
  assert.equal(preparationLabel(absent), "Not run");
  assert.equal(matchesPreparation(absent, "not_prepared"), true);
});

test("preparation attempts expose their status without counting failures as prepared", () => {
  for (const status of [
    "running",
    "queued",
    "failed",
    "canceled",
    "interrupted",
  ]) {
    const [row] = withTaskFields(
      [candidate()],
      [attempt("1", { kind: "prepare", status })],
    );
    assert.equal(preparationLabel(row).toLowerCase(), status);
    assert.equal(matchesPreparation(row, "prepared"), false);
  }
});

test("derived fields preserve the original candidates and reject invalid reports", () => {
  const original = candidate();
  const [row] = withTaskFields(
    [original],
    [attempt("1", { ram_estimate: { per_process: ram(-1), total: null } })],
  );
  assert.equal(row.fields.candidate_ram_per_process_gib, "");
  assert.deepEqual(original.fields, { MolecularWeight: "100" });
  assert.equal(original.task_summary, undefined);
});

test("RAM sorting keeps missing and invalid values last in both directions", () => {
  const rows = [
    "",
    "170.87",
    "Unavailable",
    "111.84",
    "0",
    "   ",
    "NaN",
    undefined,
  ].map((value, index) => ({
    id: String(index),
    fields: { complex_ram_per_process_gib: value },
  }));
  assert.deepEqual(
    sortCandidates(rows, "complex_ram_per_process_gib", "asc").map((r) => r.id),
    ["4", "3", "1", "0", "2", "5", "6", "7"],
  );
  assert.deepEqual(
    sortCandidates(rows, "complex_ram_per_process_gib", "desc").map(
      (r) => r.id,
    ),
    ["1", "3", "4", "0", "2", "5", "6", "7"],
  );
  assert.equal(rows[0].id, "0");
});

test("RAM display rounds to whole GB without changing numeric sorting or filtering", () => {
  const rows = [111.84, 111.6].map((value, index) => ({
    id: String(index),
    fields: { candidate_ram_per_process_gib: String(value) },
  }));
  assert.equal(ramDisplay(rows[0], "candidate"), "112 GB");
  assert.equal(ramDisplay(rows[1], "candidate"), "112 GB");
  assert.equal(ramDisplay(rows[0], "complex"), "Unavailable");
  assert.deepEqual(
    sortCandidates(rows, "candidate_ram_per_process_gib", "asc").map(
      (r) => r.id,
    ),
    ["1", "0"],
  );
  assert.equal(
    matchesFilters(rows[0], [
      { field: "candidate_ram_per_process_gib", operator: "<", value: "111.7" },
    ]),
    false,
  );
});

test("AG Grid comparator keeps unavailable values last despite descending inversion", () => {
  for (const descending of [false, true]) {
    const values = [null, 170.87, 111.84, 0, NaN];
    const sorted = [...values].sort(
      (a, b) => compareGridNumbers(a, b, descending) * (descending ? -1 : 1),
    );
    assert.deepEqual(
      sorted.slice(0, 3),
      descending ? [170.87, 111.84, 0] : [0, 111.84, 170.87],
    );
    assert.equal(sorted[3], null);
    assert.ok(Number.isNaN(sorted[4]));
  }
  assert.equal(numericValue("   "), null);
  assert.equal(numericValue("Unavailable"), null);
  assert.equal(numericValue(undefined), null);
  assert.equal(numericValue("0"), 0);
});
