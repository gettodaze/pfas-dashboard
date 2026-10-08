import { useEffect, useMemo, useRef } from "react";
import { AgGridReact } from "ag-grid-react";
import {
  ClientSideRowModelModule,
  RowSelectionModule,
  RowApiModule,
  ColumnApiModule,
  CellStyleModule,
  GridStateModule,
  ModuleRegistry,
  themeQuartz,
  type ColDef,
  type ColGroupDef,
  type GridApi,
  type GridState,
  type ICellRendererParams,
} from "ag-grid-community";
import type { Action, Candidate, Data, Task } from "./data";
import {
  compareGridNumbers,
  numericValue,
  preparationLabel,
  ramDisplay,
} from "./candidateResults";
import { PubChemLink } from "./PubChemLink";
import { readView, saveView } from "./viewStorage";

const gridViewKey = "pfas.candidate-grid.v1";
function saveGridView(state: GridState) {
  const {
    version,
    columnOrder,
    columnSizing,
    columnPinning,
    columnVisibility,
    scroll,
  } = state;
  saveView(gridViewKey, {
    version,
    columnOrder,
    columnSizing,
    columnPinning,
    columnVisibility,
    scroll,
    partialColumnState: true,
  });
}

ModuleRegistry.registerModules([
  ClientSideRowModelModule,
  RowSelectionModule,
  RowApiModule,
  ColumnApiModule,
  CellStyleModule,
  GridStateModule,
]);
const theme = themeQuartz.withParams({
  fontFamily: "system-ui, sans-serif",
  fontSize: 13,
  headerBackgroundColor: "#e3edef",
  headerTextColor: "#153c46",
  selectedRowBackgroundColor: "#edf4f8",
});
const defaultColDef: ColDef<Candidate> = {
  sortable: true,
  resizable: true,
  initialFlex: 1,
  minWidth: 70,
  wrapHeaderText: true,
  autoHeaderHeight: true,
  suppressHeaderMenuButton: true,
  sortingOrder: ["asc", "desc"],
  cellDataType: false,
};
const comparator: ColDef<Candidate>["comparator"] = (
  a,
  b,
  _left,
  _right,
  descending,
) => compareGridNumbers(a, b, descending);

export function CandidateGrid({
  candidates,
  data,
  busy,
  action,
  selected,
  onSelection,
  sort,
  direction,
  onSort,
  diagrams,
  latestDiagram,
  prepared,
  active,
  onRun,
}: {
  candidates: Candidate[];
  data: Data;
  busy: boolean;
  action: (path: string, body?: Action) => Promise<void>;
  selected: Set<string>;
  onSelection: (ids: Set<string>) => void;
  sort: string;
  direction: string;
  onSort: (field: string, direction: string) => void;
  diagrams: Map<string, Task>;
  latestDiagram: Map<string, Task>;
  prepared: Map<string, Task>;
  active: Set<string>;
  onRun: (candidate: string, system: string) => void;
}) {
  const grid = useRef<AgGridReact<Candidate>>(null);
  const initialState = useMemo(() => readView<GridState>(gridViewKey), []);
  const live = data.mode === "live";
  const ramField =
    sort === "candidate_ram_per_process_gib"
      ? sort
      : "complex_ram_per_process_gib";
  function syncSelection(api: GridApi<Candidate>) {
    api.forEachNode((node) => {
      if (
        node.data &&
        Boolean(node.isSelected()) !== selected.has(node.data.id)
      )
        node.setSelected(selected.has(node.data.id));
    });
  }
  useEffect(() => {
    if (grid.current?.api) syncSelection(grid.current.api);
  }, [selected, candidates]);
  const rowSelection = useMemo(
    () =>
      live
        ? {
            mode: "multiRow" as const,
            enableClickSelection: false,
            selectAll: "filtered" as const,
          }
        : undefined,
    [live],
  );
  const selectionColumnDef = useMemo(
    () => ({
      width: 44,
      minWidth: 44,
      maxWidth: 44,
      resizable: false,
      suppressMovable: true,
    }),
    [],
  );
  const columns = useMemo<
    (ColDef<Candidate> | ColGroupDef<Candidate>)[]
  >(() => {
    function numeric(
      field: string,
      label: string,
      extra: ColDef<Candidate> = {},
    ): ColDef<Candidate> {
      return {
        colId: field,
        headerName: label,
        headerClass:
          field === "medoid_CID" ? "representative-heading" : "cluster-heading",
        cellClass:
          field === "medoid_CID" ? "representative-cell" : "cluster-cell",
        valueGetter: (p) => numericValue(p.data?.fields[field]),
        comparator,
        sort: sort === field ? (direction as "asc" | "desc") : null,
        ...extra,
      };
    }
    return [
      {
        headerName: "Cluster",
        headerClass: "cluster-heading",
        children: [
          numeric("cluster", "ID", {
            valueGetter: (p) => numericValue(p.data?.id),
            cellRenderer: (p: ICellRendererParams<Candidate>) =>
              p.data && <a href={`#candidate/${p.data.id}`}>{p.data.id}</a>,
          }),
          numeric("n_points", "Points"),
          numeric("MolecularWeight", "Avg MW", {
            valueFormatter: (p) =>
              p.value === null ? "Unavailable" : Number(p.value).toFixed(2),
          }),
          numeric("XLogP", "Avg XLogP", {
            valueFormatter: (p) =>
              p.value === null ? "Unavailable" : Number(p.value).toFixed(2),
          }),
        ],
      },
      {
        headerName: "Representative",
        headerClass: "representative-heading",
        children: [
          numeric("medoid_CID", "CID", {
            minWidth: 95,
            cellRenderer: (p: ICellRendererParams<Candidate>) =>
              p.data && <PubChemLink cid={p.data.cid} />,
          }),
          {
            colId: "diagram",
            headerClass: "representative-heading",
            cellClass: "representative-cell",
            headerName: "Diagram",
            sortable: false,
            minWidth: 210,
            initialFlex: 2.5,
            cellRenderer: (p: ICellRendererParams<Candidate>) => {
              if (!p.data) return null;
              const diagram = diagrams.get(p.data.id),
                attempt = latestDiagram.get(p.data.id);
              return (
                <a href={`#candidate/${p.data.id}`} className="grid-diagram">
                  {diagram ? (
                    <img
                      loading="lazy"
                      className="thumbnail"
                      src={diagram.artifacts["diagram.png"]}
                      alt={`Representative molecule for cluster ${p.data.id}`}
                    />
                  ) : (
                    <span title={attempt?.error}>
                      {attempt
                        ? `Diagram ${attempt.status}`
                        : "Diagram not generated"}
                    </span>
                  )}
                </a>
              );
            },
          },
          {
            colId: "ram",
            headerClass: "representative-heading",
            cellClass: "representative-cell",
            headerName: "Est RAM",
            minWidth: 200,
            initialFlex: 2,
            valueGetter: (p) => numericValue(p.data?.fields[ramField]),
            comparator,
            sort: sort === ramField ? (direction as "asc" | "desc") : null,
            cellRenderer: (p: ICellRendererParams<Candidate>) =>
              p.data && (
                <div className="grid-ram">
                  <div>
                    Candidate:{" "}
                    <strong>{ramDisplay(p.data, "candidate")}</strong>
                  </div>
                  <div>
                    Complex: <strong>{ramDisplay(p.data, "complex")}</strong>
                  </div>
                </div>
              ),
          },
          {
            colId: "actions",
            headerClass: "representative-heading",
            cellClass: "representative-cell",
            headerName: "Actions",
            sortable: false,
            minWidth: 185,
            initialFlex: 2,
            cellRenderer: (p: ICellRendererParams<Candidate>) => {
              const c = p.data;
              if (!c) return null;
              const preparation = prepared.get(c.id),
                preparing = active.has(`${c.id}/prepare/candidate`);
              const statusClass = preparation
                ? "preparation-ready"
                : "preparation-missing";
              if (!live)
                return (
                  <span className={`preparation-status ${statusClass}`}>
                    {preparationLabel(c)}
                  </span>
                );
              return (
                <div className="row-actions">
                  <button
                    disabled={busy || preparing}
                    className={statusClass}
                    aria-label={`${preparing ? "Preparing inputs" : preparation ? "Prepare inputs again" : "Prepare inputs"} for cluster ${c.id}${preparation ? " (already prepared)" : " (not prepared)"}`}
                    title={`${preparationLabel(c)}${c.task_summary?.latestPreparation ? ` · latest attempt: ${c.task_summary.latestPreparation.status}` : ""}`}
                    onClick={() =>
                      action("tasks", {
                        kind: "prepare",
                        candidate: c.id,
                        runtime: data.queue?.runtimes.apptainer.available
                          ? "apptainer"
                          : "native",
                      })
                    }
                  >
                    {preparing
                      ? "Preparing…"
                      : preparation
                        ? "Prepare again"
                        : "Prepare inputs"}
                  </button>
                  {[
                    { system: "candidate", label: "Run QE · candidate" },
                    { system: "complex", label: "Run QE · complex" },
                  ].map(({ system, label }) => (
                    <button
                      key={system}
                      disabled={
                        busy ||
                        !preparation?.artifacts[`${system}.in`] ||
                        active.has(`${c.id}/qe/${system}`)
                      }
                      title={
                        !preparation?.artifacts[`${system}.in`]
                          ? "Prepare inputs first"
                          : "Review the command and input before submitting"
                      }
                      onClick={() => onRun(c.id, system)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              );
            },
          },
        ],
      },
    ];
  }, [
    sort,
    direction,
    ramField,
    diagrams,
    latestDiagram,
    prepared,
    active,
    live,
    busy,
    action,
    data.queue,
    onRun,
  ]);
  return (
    <div
      className="candidate-grid-table"
      aria-label="Cluster and representative table"
    >
      <AgGridReact<Candidate>
        ref={grid}
        theme={theme}
        initialState={initialState}
        onStateUpdated={(e) => saveGridView(e.state)}
        onGridPreDestroyed={(e) => saveGridView(e.state)}
        rowData={candidates}
        columnDefs={columns}
        maintainColumnOrder={true}
        defaultColDef={defaultColDef}
        getRowId={(p) => p.data.id}
        rowHeight={160}
        headerHeight={50}
        groupHeaderHeight={32}
        animateRows={false}
        suppressMultiSort={true}
        rowSelection={rowSelection}
        selectionColumnDef={selectionColumnDef}
        onGridReady={(e) => syncSelection(e.api)}
        onRowDataUpdated={(e) => syncSelection(e.api)}
        onSelectionChanged={(e) => {
          if (e.source !== "api" && e.source !== "rowDataChanged")
            onSelection(new Set(e.api.getSelectedRows().map((c) => c.id)));
        }}
        onSortChanged={(e) => {
          if (e.source !== "uiColumnSorted") return;
          const column = e.api.getColumnState().find((c) => c.sort);
          if (column)
            onSort(
              column.colId === "ram" ? ramField : column.colId,
              column.sort!,
            );
        }}
      />
    </div>
  );
}
