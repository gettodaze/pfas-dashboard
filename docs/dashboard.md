# PFAS dashboard

From the repository root, with uv and Node/npm installed:

```bash
uv run --no-default-groups --group web python -m dashboard
```

Open http://localhost:8000. The launcher installs the locked npm dependencies,
checks TypeScript, builds React, and serves the frontend and FastAPI from one
process. Python dependencies live in the root `pyproject.toml` and `.venv`.
The `web` group includes FastAPI, Uvicorn, and RDKit for diagrams. Add the
`preparation` group (which includes `chemistry` and `qe`) to enable input generation:

```bash
uv run --no-default-groups --group web --group preparation python -m dashboard
```

The launcher and preparation subprocess use the same Python interpreter. For an
existing external chemistry environment, `PFAS_CHEM_PYTHON` remains an optional
override. `--no-default-groups` keeps unrelated ML/data dependencies out of these
commands; the repository's existing default-groups setting remains available.
The dashboard displays **all 1,000 clusters (0–999)** from
`shivani_ml_models/cluster_centers.csv`. CSV descriptors are
cluster averages. CID and SMILES identify each cluster's representative molecule;
all original fields are retained in its detail view.

The default tile view shows five diagrams across on wide screens and adapts to
smaller screens. Click a tile to open its entry. Switch to Rows for descriptors
and live action buttons. Both views share a quick filter for **cluster IDs only**,
a preparation filter, and sorting by CSV or RAM fields,
with ascending/descending numerical sorting for numeric descriptors and IDs.
The ID filter accepts exact IDs, inclusive ranges, and comma-separated unions:
`20-50`, `1,3,5`, and `2-10, 15`. Invalid syntax is flagged beside the filter.
CSV order remains available. The Advanced search page combines numeric conditions
with AND, for example Avg MW < 500 and Avg XLogP >= 2. Applied conditions remain
visible above the results and can be cleared. Missing values do not match.

The candidate browser fills the available window width with small margins.
Its AG Grid Community table has centered headers, resizable columns, virtualized
rows, compact action buttons, and larger
representative diagrams. **Actions for selected molecules** appears only after
selecting at least one molecule.

Rows have one **RAM estimate (GB)** column with Candidate and Complex values,
rounded to the nearest whole GB. Each value uses that system's latest successful
QE maximum per-process estimate, converted using QE's binary GB convention.
Missing estimates display Unavailable. Version, process count, and reported total
remain in task history; missing totals are never inferred. These are historical
estimates, not measured memory or a claim about the current edited input.

Choose **RAM · Candidate** or **RAM · Complex** in Sort by, or click the RAM
header to sort complex RAM initially and toggle the selected RAM sort direction.
Click other numeric headers to sort their columns. Grid checkboxes share the
same selection as tiles and the Select all matching toolbar; changing filters
drops hidden selections. Bulk jobs retain the displayed sort order.
Sorting uses full precision; missing and invalid values remain last in both
directions. RAM filtering is available in **Advanced search**, including totals
when reported, and combines with descriptor conditions. Missing values do not
match numeric filters.

Preparation status appears in Actions: green **Prepare again** means preparation
previously succeeded, red **Prepare inputs** means it has not, and disabled
**Preparing…** marks a queued or running preparation. Accessible text and
tooltips identify the status without relying on color. Snapshots show a colored
status label instead of an executable button. The preparation filter distinguishes
previously successful preparations from candidates without one.

Rows group Cluster descriptors, RAM, and Representative information. CIDs in rows, tiles, and entries
open the PubChem compound's 3D Status section in a new tab. The table uses wrapping
action buttons and a bounded scroll area so its scrollbars remain
accessible on narrow screens and at larger zoom levels. Row buttons prepare inputs or open QE controls below the grid
for the isolated candidate (single) and candidate–TFA complex. Preparation must
succeed before those run buttons become available; preview the command and input
before submitting. Snapshot views include tiles, rows, search, and sorting, with
execution controls available only in live mode.

Browsing never starts work. Use Generate diagram, Prepare inputs, or preview
and submit a QE run. Refresh updates the queue, elapsed time, and bounded log
tails. Native tasks run serially; verified Apptainer tasks can run within resource
budgets. Preparation and QE are independent actions, including bulk batches;
there is no automatic dependency execution or adsorption aggregation.

## Chemistry configuration

The `preparation` dependency group supplies RDKit, pymatgen, ASE, Open Babel,
and cif2cell. The selected interpreter's bin directory is prepended to PATH for
preparation, so `obabel` and `cif2cell` are resolved from the same environment.
For an existing external chemistry environment, you can instead set:

```bash
export PFAS_CHEM_PYTHON="/path/to/chemistry/bin/python"
export PFAS_PSEUDOS="$PWD/qespresso_pipeline/Pseudopotentials"
uv run --no-default-groups --group web python -m dashboard
```

Missing chemistry tools or element pseudopotentials reject preparation before
it enters the queue; browsing still works. Select `--group preparation` to install the chemistry dependencies via uv.
Diagrams work with `--group web` alone;
if `PFAS_CHEM_PYTHON` is set, that interpreter must also provide RDKit. The preparation child entry point is
`dashboard/preparation.py`; it reuses conversion, molecular-complex, and
patching helpers and never executes QE.

Inputs use the molecular `cluster` preset: PBE, D3, 60/600 Ry cutoffs, Gamma
sampling, mixing beta 0.15, 12 Å padding, 2.5 Å complex gap, spin 1, and relaxation.
The shared reference is **neutral TFA**, `O=C(O)C(F)(F)F`, CID 8442. Preparation
creates the reference if absent and otherwise reuses a compatible successful
reference, recording its source attempt. A candidate preparation creates
isolated-candidate and candidate–TFA inputs. Manifests record settings, source
CID/SMILES, and input/pseudopotential SHA-256 hashes. Representative geometries
and complex placement are starting models, not optimized adsorption structures.

`PFAS_PREPARE_TIMEOUT` sets the preparation timeout in seconds (default 900).
Diagrams run in isolated children with a 60-second timeout. Bulk diagrams skip
existing successful PNGs, deduplicate active attempts, and continue after failures.

## Local QE and MPI

For native execution, `PFAS_PW` and `PFAS_MPI` override the `pw.x` and `mpirun`
executable paths. Otherwise both resolve through PATH. Apptainer execution uses
the image's QE and MPI executables. MPI is required only for more than one process.
Commands use argument lists directly, without a shell:

```text
pw.x -in input.in
mpirun -np N pw.x -in input.in
```

`OMP_NUM_THREADS=1` for all runs. Select a positive integer process count (default
one), preview the exact command and input, then submit. There are separate Run QE
controls for TFA, candidate, and complex. Slurm and Slurm array return HTTP 501
before creating a task. They do not submit anything.

Each run gets a new directory with an input captured when queued, immutable
pseudopotential copies with matching links, and its own `Outputs` directory.
Externally edited preparation inputs are accepted only with local
`pseudo_dir='./Pseudopotentials'` and `outdir='./Outputs'`. An input changed since
preview requires another preview. QE timeout is optional; by default it has no
application-imposed timeout. Stop terminates the entire process group, including
MPI children. Full stdout/stderr downloads become available when the attempt
finishes. The UI separately reports process exit, JOB DONE, SCF convergence,
relaxation completion, and provisional/confirmed last energies.

The candidate browser remembers Rows/Tiles, sorting, cluster ID and preparation
filters in this browser. The row table also restores column widths, order, and
scroll position after refresh or navigation. These preferences use local browser
storage; clearing site data resets them.

## Persistence and development

`PFAS_ARTIFACTS` selects persistent local storage (default `.dashboard/`). Keep the
SQLite database on local disk. Attempts and logs are preserved on retry. Graceful
shutdown stops active children and leaves waiting jobs queued. On startup,
queued jobs resume automatically in their original order, unless the queue is
paused. Attempts that were running are marked interrupted and require an explicit
retry; a stopped QE calculation is not resumed midway. A forced kill may leave
child processes alive, so check those before retrying an interrupted attempt.
The server terminal logs queued/started/finished attempts,
preparation rejections, failures, and each attempt's full log paths. Preparation
Python output is unbuffered so it is available while the task runs. Follow an
attempt in another terminal using the paths printed by the server:

```bash
tail -f .dashboard/<task-id>/stdout.log .dashboard/<task-id>/stderr.log
```

Task history includes bounded stdout/stderr tails and full-log downloads when an
attempt finishes. Preflight rejection occurs before creating an attempt and is
reported in the browser and server terminal. Run only one dashboard server against a given artifact directory.

```bash
uv run --no-default-groups --group web python -m dashboard --dev
```

Open the Vite URL (normally http://localhost:5173). Vite proxies `/api` to FastAPI;
both stop on interrupt. React uses built-in state, `fetch`, manual refresh for
candidate data, lightweight polling for the queue page, and hash navigation. Components live under `web/frontend/src`. Plain CSS includes
narrow-screen layouts and a horizontally scrollable table.

The launcher accepts `--host` and `--port`; it binds to loopback by default.
Mutation routes require POST and reject cross-origin browser requests. Binding
to another interface **does not add authentication**. Keep access local or tunneled.

For a future PACE session, the proposed approach is to install the repository and
chemistry environment on a permitted compute node, run the server on loopback
inside an allocated job, and use SSH local forwarding through the PACE login
host. For example, adapt `ssh -L 8000:compute-node:8000 your-pace-login` to the
site's permitted tunnel topology. PACE connectivity and policy compatibility
remain **unverified**; Slurm execution and a shared live service are deferred.

## Read-only public snapshot

```bash
uv run --no-default-groups --group web python -m dashboard export
```

Export does not trigger chemistry or QE. It builds the frontend, writes committed
public data under `web/snapshot/`, and assembles the static site under ignored
`web/public/` (use `--output` to change the assembled-site destination). Commit the
snapshot data to update the public dashboard. Only summary JSON, existing
successful diagrams, prepared coordinates, and selected result metadata are exported. Raw logs,
databases, machine paths, scratch files, credentials, and mutation controls are
excluded. The UI shows the export timestamp and local launch instructions.

The `dashboard.yml` workflow tests the backend, builds the frontend, copies
**committed** snapshot data, and deploys to GitHub Pages on main or manual dispatch.
Configure the repository Pages source as GitHub Actions. Publication depends on
that repository setting. Assets and snapshot requests are relative; candidate
links use hashes and work under `/pfas-environment-cleanup/` without server routing.

## Validation

```bash
uv run --locked --no-default-groups --group web --group dev pytest web/tests -m 'not slow'
uv run --locked ty check
uv run --locked ruff check dashboard web/tests
npm run build --prefix web/frontend
npm run format:check --prefix web/frontend
```

Opt-in smoke checks use real tools, isolated temporary directories, a 90-second
preparation timeout and 45-second QE timeouts. The small H2 SCF fixture checks
native one- and two-process execution; it does not validate production scientific
accuracy or convergence of the candidates:

```bash
PFAS_SMOKE=1 uv run --locked --no-default-groups --group web --group preparation --group dev \
  pytest web/tests/test_smoke.py -q
```

Deferred: relaxed-geometry viewing, automatic workflows, adsorption aggregation,
Slurm submission, authentication, and a shared live service.

## Initial geometry viewer

Open a prepared cluster entry (for example `#candidate/501`) and find **Initial
geometry**. Select the isolated candidate, candidate–TFA complex, or neutral TFA;
drag to rotate, use the zoom buttons, or download XYZ coordinates. The TFA
reference page also offers this viewer after reference preparation. Existing
preparations work without rerunning them. Coordinates come from the current
prepared QE input, including external edits, converted to angstroms; this is the
starting geometry, not a relaxed result. Bonds are inferred only for display.

Live builds request `/api/data` directly. Snapshot builds request relative
`snapshot/data.json` directly, so a missing snapshot no longer produces a 404
probe during local browsing. The export command and Pages workflow select the
snapshot build automatically; for a manual frontend snapshot build use
`PFAS_DATA_MODE=snapshot npm run build --prefix web/frontend`. Exports also
include prepared coordinates and input hashes, without raw input text or local
paths.

## Selected batches and job queue

In tiles or rows, filter cluster IDs (for example `500-670`), choose **Average
MolecularWeight / Ascending**, then **Select all matching**. This range includes
171 clusters; sorting changes their start order, not their identities. MW is a
cluster average, not the representative molecule's molecular weight. Checkboxes
allow individual selection. Changing filters drops hidden selections; switching
views or changing sorting preserves selections.

Choose **Generate missing diagrams**, **Prepare missing inputs**, **Run QE ·
isolated candidate**, or **Run QE · TFA complex**, then **Preview selected jobs**.
Review eligibility, resource settings, and QE input text before **Queue jobs**.
Batch review displays 50 jobs per page and renders exact input text when its
details are opened. Preview and submission show elapsed time; server logs report
review and acceptance progress every 100 entries. Numeric fields can be cleared
while editing; action buttons wait for valid resource values. RAM estimation
uses one process; its result is labeled **Est RAM**.
Completed preparations/diagrams with available artifacts are skipped. QE skips
only confirmed results with matching input and pseudopotential hashes. Active
actions are deduplicated. Each selected representative receives a separate
attempt; failures do not stop the batch. Preparation never automatically runs
QE, and QE never automatically prepares a missing input.

The **Job queue** page polls lightweight state every two seconds. Jobs and batch
entries are paginated in groups of 50; batches are paginated in groups of 10.
Task logs include UTC timestamps and a task/hash header. Each task also has an
`entrypoint.log` with its full launch command, working directory, hashes, and
resource settings, available in the log viewer and as a download. The page shows
batches, attempts, current/peak memory, and separate log-tail/full-log controls.
Pause prevents new jobs from starting while active jobs finish. Stop cancels one
job; Cancel batch stops its active jobs and cancels pending jobs. Running jobs
show **Stopping…** after a stop request; batches with no active jobs cannot be
canceled again. **Clear queue** cancels all waiting jobs across batches and
individual submissions, keeping running jobs, history, and artifacts intact.
**Review
unsuccessful jobs** creates a fresh preview, including incomplete QE results;
submission creates new attempts. Shutdown/restart preserves waiting jobs and
resumes them automatically, respecting
the saved pause setting and resource budgets. Running attempts become interrupted
and require an explicit retry. Queue settings and batches live in the
existing local SQLite database; existing history remains readable.

### Native and Apptainer execution

Native execution works with the existing Python/QE configuration and stays
serial, including when the container concurrency setting exceeds one. Native
jobs have timeouts, process-group Stop, and measured process-tree RSS, but **no
hard memory cap**. MPI processes within one QE job still use the selected process
count. To protect WSL with enforced per-job limits, select Apptainer.

Install Apptainer using its [official installation instructions](https://apptainer.org/docs/admin/latest/installation.html), then build the
image explicitly from the repository root:

```bash
mkdir -p .container-build
apptainer build --fakeroot .container-build/chemistry.sif containers/chemistry.def
export PFAS_APPTAINER_IMAGE="$PWD/.container-build/chemistry.sif"
uv run --no-default-groups --group web python -m dashboard
```

The definition uses Ubuntu 24.04, Python 3.12, the root locked Python dependency
groups, and QE 7.6 compiled from the `qe-7.6` release against Ubuntu's Open MPI.
It builds `pw.x` with two compiler jobs and fails if MPI support is absent.
The image test initializes a small hydrogen system in a writable temporary
directory using two MPI processes, requiring the QE 7.6 banner, two-process MPI
banner, and `JOB DONE`. It performs no SCF iterations. Do not skip this test.
Source compilation makes the first build slower than a package installation.
The definition also includes native build tools for Open Babel bindings.
Building with `--fakeroot` requires a correctly configured
Apptainer installation; alternatively build on a supported host and copy the
SIF. Do not commit images. The image records installed system/Python versions in
`/opt/pfas-image/`, including the QE source commit and build configuration;
Python dependencies are locked, but Ubuntu package repositories
can change between builds. Preserve the resulting SIF for exact reuse. The
runtime records its SHA256 and rejects queued work if the image changes.
`PFAS_APPTAINER` optionally overrides the executable path.

The web server runs on the host. Chemistry and QE run inside the image, with
repository code/pseudopotentials read-only and each attempt directory writable.
Container QE uses its own MPI, not host MPI. Apptainer requires cgroups v2 and a
working systemd user service for unprivileged memory limits. The dashboard runs
a bounded probe and checks the actual memory and swap limits before accepting
container work, then verifies enforcement again before each job starts. Failed
verification never silently falls back to native. Select native explicitly if
you want to proceed without hard isolation.

Default container limits are **4 GiB/job**, **no additional swap**, and **one
concurrent job**. Once a container preview verifies enforcement, increase
**Maximum container jobs** on the queue page if desired. Defaults are an **8 GiB
total reservation budget**, reduced to 90% of the detected memory ceiling on
smaller hosts, and at most **four available CPUs**. A job must fit both budgets;
start order remains FIFO even when later smaller jobs could fit. Native work
never overlaps any other job. Container reservations use limits, not current
usage. The total memory budget can use up to 90% of detected host memory; other processes
remain outside these limits. Preparation/BLAS threads are limited to one; MPI
processes count toward CPU reservations. Preparation defaults to 900 seconds,
diagrams to 60 seconds, and QE has no timeout unless configured.

Memory-limit failures are identified from cgroup OOM evidence, rather than
assuming every exit 137 means OOM. Container memory accounting includes child
processes and file cache; native RSS sums may double-count shared pages. Logs
and partial artifacts survive failures. Slurm submission remains unavailable:
the shared job specification separates runtime/image from CPU, memory, walltime,
input snapshots, and outcome fields, ready for a later scheduler adapter. PACE
MPI compatibility must be tested before introducing cluster execution; the
legacy scripts document a native QE build separately from this local image.

### Additional validation

```bash
uv run --locked --no-default-groups --group web --group dev pytest -m 'not slow'
PFAS_SMOKE=1 uv run --locked --no-default-groups --group web --group preparation --group dev pytest web/tests/test_smoke.py -q
PFAS_CONTAINER_SMOKE=1 uv run --locked --no-default-groups --group web --group dev pytest web/tests/test_container_smoke.py -q
```

Container checks require a configured built image and exercise real preparation,
QE with one/two processes, a deliberately tiny 128 MiB OOM cap, descendant cleanup,
and a subsequent successful job. They never build/install the runtime implicitly.

### Local input versions and RAM estimates

Keep externally edited QE inputs in `qe_inputs/<candidate-id>/`, for example
`qe_inputs/500/candidate.large-cell.in` or
`qe_inputs/500/complex.close-contact.in`. Shared reference versions belong in
`qe_inputs/tfa/tfa.<version>.in`. Set `PFAS_QE_INPUTS` to change the root.
Refresh the dashboard to discover new files; a missing root is allowed. Only
named versions contained within that root are discovered. Select **Prepared
default** to use the latest successful preparation, or select a local version
without preparing first. Batch controls use prepared inputs, including
**Estimate RAM · isolated candidate** and **Estimate RAM · TFA complex**.
Select molecules, preview the selected jobs, and queue eligible estimates.
Each estimate uses the selected process count and runtime, defaults to 120
seconds, and captures its own initialization-only input. The review skips active
estimates and successful estimates matching the source input, pseudopotentials,
runtime/image, and process count. Check **Rerun existing estimates** before
previewing to queue fresh RAM estimates even when matching successful results
exist. Active jobs are still skipped. Changing this checkbox requires a new
preview. Missing prepared inputs are listed as
unavailable. Results appear in the queue and individual task history.
**Review unsuccessful jobs** requires a fresh preview before retrying.

Choose **Run QE** or **Estimate RAM** and select the runtime. QE runs allow a
process count; dashboard RAM estimates always use one process. Then
then preview the input, command, and initial geometry before submission.
Estimates default to a 120-second timeout; the timeout field overrides it.
The estimate captures a copy with `&CONTROL nstep=0`, leaving the original
editable file unchanged. QE documents this initialization-only mode in its
[input reference](https://www.quantum-espresso.org/Doc/INPUT_PW.html).
A successful initialization may intentionally return exit code 255, as shown
in [QE's dry-run implementation](https://github.com/QEF/q-e/blob/master/PW/src/run_pwscf.f90).

Inputs must use `pseudo_dir='./Pseudopotentials'` and `outdir='./Outputs'`,
with any explicit `wfcdir` also set to `./Outputs`. Pseudopotentials come from
`PFAS_PSEUDOS`. Preview rejects unsafe references and missing pseudopotentials.
Submission rejects source or pseudopotential changes since preview. Each
queued attempt captures immutable input and pseudopotential copies, version
label, source and executed-input SHA256 hashes, runtime, and process count.
Retry requires a fresh preview; later edits or deletion do not alter queued
attempts. New API clients submit the returned `preview_id`, `input_hash` as
`expected_hash`, and optional `input_id` with their task request. Omitting
`input_id` retains prepared-default behavior for existing clients.

History displays **Est RAM** for one-process estimates separately from measured process memory.
Reported MB/GB values are retained and converted to bytes using binary units
(1024²/1024³). The display uses QE's maximum RAM report from the single process;
total RAM fields are not displayed. Older estimates using multiple processes
do not populate the candidate's Est RAM value. Estimates do not contribute energy or
convergence evidence and do not adjust memory limits. A missing per-process
report or QE error fails the estimate; cancellation and timeout remain failures
or canceled attempts. Snapshot exports retain version labels, hashes, and
estimates while omitting local paths and raw inputs.

Run the opt-in native estimate smoke check with
`PFAS_RAM_SMOKE=1 .venv/bin/python -m pytest web/tests/test_ram_smoke.py -o session_timeout=180`.
It needs an installed `pw.x` (or `PFAS_PW`) and `H.UPF` under `PFAS_PSEUDOS`.
