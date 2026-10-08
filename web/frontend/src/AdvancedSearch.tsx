import { useState } from "react";
import { ramFields } from "./candidateResults";
import { Candidate } from "./data";
import { Filter, filterFields, matchesFilters } from "./filters";

export function AdvancedSearch({
  candidates,
  filters,
  apply,
}: {
  candidates: Candidate[];
  filters: Filter[];
  apply: (filters: Filter[]) => void;
}) {
  const [draft, setDraft] = useState<Filter[]>(
    filters.length
      ? filters
      : [{ field: "MolecularWeight", operator: "<", value: "500" }],
  );
  const valid = draft.every(
    (f) => Boolean(f.value.trim()) && Number.isFinite(Number(f.value)),
  );
  const count = valid
    ? candidates.filter((c) => matchesFilters(c, draft)).length
    : null;
  const change = (i: number, update: Partial<Filter>) =>
    setDraft(draft.map((f, j) => (i === j ? { ...f, ...update } : f)));
  return (
    <section>
      <h1>Advanced search</h1>
      <p>
        Combine numeric conditions. A cluster must match every condition.
        Average descriptors describe the cluster; CID identifies its
        representative molecule. RAM fields use the latest successful QE
        estimate for each system, in GB using QE’s binary units. Missing values
        do not match.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) apply(draft);
        }}
      >
        {draft.map((f, i) => (
          <fieldset className="filter-condition" key={i}>
            <legend>Condition {i + 1}</legend>
            <div className="controls">
              <label>
                Field{" "}
                <select
                  aria-label={`Field ${i + 1}`}
                  value={f.field}
                  onChange={(e) => change(i, { field: e.target.value })}
                >
                  <optgroup label="Cluster">
                    {Object.entries(filterFields)
                      .filter(
                        ([key]) => key !== "medoid_CID" && !(key in ramFields),
                      )
                      .map(([key, label]) => (
                        <option key={key} value={key}>
                          {label}
                        </option>
                      ))}
                  </optgroup>
                  <optgroup label="QE RAM estimates">
                    {Object.entries(ramFields).map(([key, label]) => (
                      <option key={key} value={key}>
                        {label}
                      </option>
                    ))}
                  </optgroup>
                  <optgroup label="Representative">
                    <option value="medoid_CID">Representative CID</option>
                  </optgroup>
                </select>
              </label>
              <label>
                Comparison{" "}
                <select
                  aria-label={`Comparison ${i + 1}`}
                  value={f.operator}
                  onChange={(e) => change(i, { operator: e.target.value })}
                >
                  {["<", "<=", ">", ">=", "=", "!="].map((op) => (
                    <option key={op} value={op}>
                      {op}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Value{" "}
                <input
                  aria-label={`Value ${i + 1}`}
                  type="number"
                  step="any"
                  required
                  value={f.value}
                  onChange={(e) => change(i, { value: e.target.value })}
                />
              </label>
              <button
                type="button"
                onClick={() => setDraft(draft.filter((_, j) => j !== i))}
              >
                Remove condition {i + 1}
              </button>
            </div>
          </fieldset>
        ))}
        <div className="controls">
          <button
            type="button"
            onClick={() =>
              setDraft([...draft, { field: "XLogP", operator: ">", value: "" }])
            }
          >
            Add condition
          </button>
          <button type="submit" disabled={!valid}>
            Apply filters
          </button>
          <button type="button" onClick={() => apply([])}>
            Clear filters
          </button>
          <a href="#">Back to clusters</a>
        </div>
        <p role="status">
          {count === null
            ? "Enter a numeric value for each condition."
            : `${count} of ${candidates.length} clusters match.`}
        </p>
      </form>
    </section>
  );
}
