import { lazy, Suspense, useMemo, useState, useEffect, useRef } from "react";
import { Action, Candidate, Data, Task } from "./data";
import { PubChemLink } from "./PubChemLink";
import {
  Filter,
  matchesFilters,
  describeFilters,
  parseClusterIDs,
  matchesClusterIDs,
  sortCandidates,
} from "./filters";
import { ramFields, matchesPreparation } from "./candidateResults";
import { BatchControls } from "./BatchControls";
const CandidateGrid = lazy(() =>
  import("./CandidateGrid").then((module) => ({
    default: module.CandidateGrid,
  })),
);
import { RunControls } from "./RunControls";
import { readView, saveView } from "./viewStorage";

const browserViewKey = "pfas.candidate-browser.v1";
type BrowserView = {
  view: string;
  search: string;
  sort: string;
  direction: string;
  preparationFilter: string;
};

function taskIndex(tasks: Task[]) {
  const diagrams = new Map<string, Task>();
  const prepared = new Map<string, Task>();
  const active = new Set<string>();
  const latestDiagram = new Map<string, Task>();
  for (const task of tasks) {
    if (task.kind === "diagram" && !latestDiagram.has(task.candidate))
      latestDiagram.set(task.candidate, task);
    if (
      task.kind === "diagram" &&
      task.status === "succeeded" &&
      task.artifacts["diagram.png"] &&
      !diagrams.has(task.candidate)
    )
      diagrams.set(task.candidate, task);
    if (
      task.kind === "prepare" &&
      task.status === "succeeded" &&
      !prepared.has(task.candidate)
    )
      prepared.set(task.candidate, task);
    if (["queued", "running"].includes(task.status))
      active.add(`${task.candidate}/${task.kind}/${task.system}`);
  }
  return { diagrams, prepared, active, latestDiagram };
}

export function CandidateBrowser({
  data,
  busy,
  action,
  filters,
  clearFilters,
  refresh,
}: {
  filters: Filter[];
  clearFilters: () => void;
  refresh: () => Promise<void>;
  data: Data;
  busy: boolean;
  action: (path: string, body?: Action) => Promise<void>;
}) {
  const [savedView] = useState(() => readView<BrowserView>(browserViewKey));
  const [view, setView] = useState(
      savedView.view === "rows" ? "rows" : "tiles",
    ),
    [search, setSearch] = useState(
      typeof savedView.search === "string" ? savedView.search : "",
    );
  const [sort, setSort] = useState(
      typeof savedView.sort === "string" ? savedView.sort : "",
    ),
    [direction, setDirection] = useState(
      savedView.direction === "desc" ? "desc" : "asc",
    );
  const [preparationFilter, setPreparationFilter] = useState(
    ["prepared", "not_prepared"].includes(savedView.preparationFilter || "")
      ? savedView.preparationFilter!
      : "all",
  );
  useEffect(() => {
    saveView(browserViewKey, {
      view,
      search,
      sort,
      direction,
      preparationFilter,
    });
  }, [view, search, sort, direction, preparationFilter]);
  const [run, setRun] = useState<{ candidate: string; system: string } | null>(
    null,
  );
  const runPanel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (run)
      runPanel.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [run]);
  const index = useMemo(() => taskIndex(data.tasks), [data.tasks]);
  const idFilter = useMemo(() => parseClusterIDs(search), [search]);
  const candidates = useMemo(
    () =>
      sortCandidates(
        data.candidates.filter(
          (c) =>
            !idFilter.error &&
            matchesPreparation(c, preparationFilter) &&
            matchesClusterIDs(c.id, idFilter.ranges) &&
            matchesFilters(c, filters),
        ),
        sort,
        direction,
      ),
    [data.candidates, idFilter, sort, direction, filters, preparationFilter],
  );
  const [selected, setSelected] = useState<Set<string>>(new Set());
  useEffect(() => {
    const visible = new Set(candidates.map((c) => c.id));
    setSelected(
      (previous) => new Set([...previous].filter((id) => visible.has(id))),
    );
  }, [candidates]);
  const selectedIDs = candidates
    .filter((c) => selected.has(c.id))
    .map((c) => c.id);
  const toggle = (id: string) =>
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const live = data.mode === "live";
  const diagramContent = (c: Candidate, tile: boolean) => {
    const diagram = index.diagrams.get(c.id);
    const attempt = index.latestDiagram.get(c.id);
    return diagram ? (
      <img
        loading="lazy"
        className={tile ? "tile-diagram" : "thumbnail"}
        src={diagram.artifacts["diagram.png"]}
        alt={`Representative molecule for cluster ${c.id}`}
      />
    ) : (
      <div
        className={tile ? "tile-placeholder" : "diagram-status"}
        title={attempt?.error || attempt?.stderr_tail}
      >
        {attempt ? `Diagram ${attempt.status}` : "Diagram not generated"}
      </div>
    );
  };
  return (
    <>
      <h1>All clusters</h1>
      <p className="browser-summary">
        Cluster descriptors and representative molecules.
      </p>
      <div className="controls browser-controls">
        <div className="view-switch" role="group" aria-label="Candidate view">
          <button
            aria-pressed={view === "tiles"}
            onClick={() => setView("tiles")}
          >
            Tiles
          </button>
          <button
            aria-pressed={view === "rows"}
            onClick={() => setView("rows")}
          >
            Rows
          </button>
        </div>
        <label>
          Filter cluster ID{" "}
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="e.g. 20-50 or 1,3,5 or 2-10, 15"
            aria-invalid={Boolean(idFilter.error)}
            aria-describedby="cluster-filter-help"
            aria-label="Filter cluster ID"
          />
        </label>
        <label>
          Preparation{" "}
          <select
            aria-label="Filter preparation"
            value={preparationFilter}
            onChange={(e) => setPreparationFilter(e.target.value)}
          >
            <option value="all">All candidates</option>
            <option value="prepared">Prepared successfully</option>
            <option value="not_prepared">No successful preparation</option>
          </select>
        </label>
        <label>
          Sort by{" "}
          <select
            aria-label="Sort by"
            value={sort}
            onChange={(e) => setSort(e.target.value)}
          >
            <option value="">CSV order</option>
            {Object.keys(data.candidates[0]?.fields || {})
              .filter((field) => !field.endsWith("_ram_total_gib"))
              .map((field) => (
                <option key={field} value={field}>
                  {(field === "candidate_ram_per_process_gib"
                    ? "Est RAM · Candidate"
                    : field === "complex_ram_per_process_gib"
                      ? "Est RAM · Complex"
                      : ramFields[field]) ||
                    (field === "cluster"
                      ? "Cluster ID"
                      : field === "n_points"
                        ? "Points"
                        : field === "medoid_CID"
                          ? "Representative CID"
                          : field === "medoid_SMILES"
                            ? "Representative SMILES"
                            : `Average ${field}`)}
                </option>
              ))}
          </select>
        </label>
        <label>
          Sort direction{" "}
          <select
            aria-label="Sort direction"
            value={direction}
            disabled={!sort}
            onChange={(e) => setDirection(e.target.value)}
          >
            <option value="asc">Ascending</option>
            <option value="desc">Descending</option>
          </select>
        </label>
      </div>
      {idFilter.error && (
        <p
          id="cluster-filter-help"
          className={idFilter.error ? "error" : "filter-help"}
          role={idFilter.error ? "alert" : undefined}
        >
          {idFilter.error}
        </p>
      )}
      {filters.length > 0 && (
        <p className="banner">
          Advanced filters: {describeFilters(filters)}.{" "}
          <a href="#advanced-search">Edit filters</a>{" "}
          <button onClick={clearFilters}>Clear advanced filters</button>
        </p>
      )}
      <p role="status">
        Showing {candidates.length} of {data.candidates.length} clusters.
      </p>
      {!candidates.length && <p>No clusters match your search.</p>}
      {live && (
        <>
          <div className="controls selection-controls">
            <button
              disabled={!candidates.length || busy}
              onClick={() => setSelected(new Set(candidates.map((c) => c.id)))}
            >
              Select all matching ({candidates.length})
            </button>
            {selectedIDs.length > 0 && (
              <button
                disabled={!selectedIDs.length}
                onClick={() => setSelected(new Set())}
              >
                Clear selection
              </button>
            )}
            {selectedIDs.length > 0 && (
              <span role="status">{selectedIDs.length} selected</span>
            )}
          </div>
          {selectedIDs.length > 0 && (
            <BatchControls
              ids={selectedIDs}
              runtimes={data.queue?.runtimes}
              onQueued={() => {
                void refresh();
                location.hash = "queue";
              }}
            />
          )}
        </>
      )}
      {view === "tiles" ? (
        <div className="candidate-grid">
          {candidates.map((c) => (
            <div
              className={`candidate-tile ${selected.has(c.id) ? "selected" : ""}`}
              key={c.id}
            >
              {live && (
                <label
                  className="tile-selection"
                  title={`Select cluster ${c.id}`}
                >
                  <input
                    type="checkbox"
                    aria-label={`Select cluster ${c.id}`}
                    checked={selected.has(c.id)}
                    onChange={() => toggle(c.id)}
                  />
                </label>
              )}
              <a
                className="tile-entry"
                href={`#candidate/${c.id}`}
                aria-label={`Open cluster ${c.id}`}
              >
                <strong className="tile-cluster">Cluster {c.id}</strong>
                {diagramContent(c, true)}
              </a>
              <span className="tile-representative">
                Representative CID <PubChemLink cid={c.cid} />
              </span>
            </div>
          ))}
        </div>
      ) : (
        <>
          <Suspense fallback={<p role="status">Loading rows…</p>}>
            <CandidateGrid
              candidates={candidates}
              data={data}
              busy={busy}
              action={action}
              selected={selected}
              onSelection={setSelected}
              sort={sort}
              direction={direction}
              onSort={(field, nextDirection) => {
                setSort(field);
                setDirection(nextDirection);
              }}
              diagrams={index.diagrams}
              latestDiagram={index.latestDiagram}
              prepared={index.prepared}
              active={index.active}
              onRun={(candidate, system) => setRun({ candidate, system })}
            />
          </Suspense>
          {live && run && (
            <section
              ref={runPanel}
              className="grid-run-controls"
              aria-label={`Run controls for cluster ${run.candidate}`}
            >
              <h3>Cluster {run.candidate}</h3>
              <button onClick={() => setRun(null)}>Close run controls</button>
              <RunControls
                key={run.candidate + run.system}
                candidate={run.candidate}
                system={run.system}
                runtimes={data.queue?.runtimes}
                versions={data.input_versions}
                action={action}
              />
            </section>
          )}
        </>
      )}
    </>
  );
}
