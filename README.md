# PFAS Adsorbent Search

## Setup

This repository ships **three** conda environment files, one per workflow. Create only the one you need.

| Environment file | Conda env name | Used for | Where it runs |
|---|---|---|---|
| `environment.yaml` | `pfas` | Data fetching and ML screening (`scripts/fetch_data.py`, `scripts/run_local_screening.sh`) | Your machine |
| `qe_environment.yaml` | `qe` | DFT adsorption runs (`qespresso_pipeline/run_adsorption_case.py`; python=3.10, qe, numpy, scipy, pandas, pymatgen, openbabel, cif2cell, ase) | The cluster — built automatically, see below |
| `basic_molecule_gnn/environment.yml` | `pfas_gnn_env` | GNN modeling in `basic_molecule_gnn/` | Your machine |

### Local quality checks

Install [uv](https://docs.astral.sh/uv/) and run these commands from the
repository root:

- Format check: `uv run --locked --only-dev ruff format --check`
- Type check: `uv run --locked ty check`
- Fast tests: `uv run --locked pytest -m 'not slow'`

To run the full local quality check sequence:

```bash
uv run --locked --only-dev ruff format --check && \
uv run --locked ty check && \
uv run --locked pytest -m 'not slow'
```

If a file should be temporarily excluded from ty, add its repository-relative path to `[tool.ty.src].exclude` in `pyproject.toml`. For a localized exception, use a `# ty: ignore[...]` comment. See the [ty configuration reference](https://docs.astral.sh/ty/reference/configuration/) and [suppression documentation](https://docs.astral.sh/ty/suppression/).

### For fetching the data

#### Installing the required packages
conda env create -f environment.yaml

conda activate pfas

`environment.yaml` lists only the direct dependencies these local workflows import, and conda resolves the rest from conda-forge. Most of the same packages are also tracked per workflow in `pyproject.toml`'s dependency groups (e.g. `data`: pandas/requests/scikit-learn; `chemistry`: rdkit/paramiko) and pinned in `uv.lock` for CI — when you add a new import, update both places.

#### Updating the environment.yaml files

conda env update -f environment.yaml --prune

#### The DFT (`qe`) environment on the cluster

You normally do **not** create the `qe` environment yourself. `scripts/run_dft_workflow.sh` (the entrypoint the SLURM jobs run) creates or updates it on the cluster automatically from `qe_environment.yaml`, at the prefix `~/.conda/envs/qe_pfas`, and then runs `qespresso_pipeline/run_adsorption_case.py` inside it. Jobs are submitted from your machine with `scripts/dft_wrapper.py` (see [Important Scripts](#important-scripts)); `run_adsorption_case.py` runs inside the SLURM job under this environment. If you need it by name for interactive use, `conda env create -f qe_environment.yaml && conda activate qe` builds the same package set. If you run `qespresso_pipeline` scripts by hand, activate this environment first — they are only supported under its Python interpreter, not a system Python (which will be missing pymatgen/ase).

## Scripts

New to the repository, or something behaving oddly? `bash scripts/verify_setup.sh`
checks your setup in one command — conda and the `pfas` environment, the DFT
toolchain, and the checkout itself — and prints one PASS / SKIP / FAIL line per
check. See [Important Scripts](#important-scripts) for what it covers.

### Running the script to fetch data

`scripts/fetch_data.py` builds the PFAS candidate dataset. With no flags it
queries the PubChem API directly: it collects candidate compounds, downloads
their properties, applies sanity filters, computes RDKit structure flags, and
writes two CSVs into `data/`:

- `data/pubchem_properties.csv` — raw PubChem properties per compound
- `data/pfas_adsorption_candidates.csv` — the merged, filtered candidate table

```
cd pfas-environment-cleanup
python3 scripts/fetch_data.py
```

If the dataset has already been generated on the PACE ICE cluster (under
`/storage/ice-shared/cs8903onl/mussmann-pfas/data/`), you can download those
CSVs over SFTP instead of re-querying PubChem:

```
cd pfas-environment-cleanup
python3 scripts/fetch_data.py --from-ice
```

This prompts for your ICE username and password (override the host or remote
directory with `--ice-host` / `--ice-remote-dir` if needed). Make sure to be
connected to the VPN.

### Screening the adsorbent library

The repository ships a 100-row adsorbent library
(`scripts/molecular_adsorbents_smiles.csv`, columns `ID,Name,SMILES,Category`)
and two ways to run DFT adsorption energies for every row against a fixed
PFAS (TFA, `FC(F)(F)C(=O)O`):

- `scripts/run_local_screening.sh` — runs the whole library sequentially on
  your machine through `scripts/run_dft_workflow.sh` (mode `lowmem`). Each
  finished case appends one line
  `case=<name> adsorption_energy_ev=<value>` to
  `scripts/master_results.txt` (harvested from
  `dft_cases/<case>/results.json`); if a case's harvest fails, its `Outputs/`
  directory is kept as debugging evidence instead of being deleted.
- `scripts/run_batch_screening.sh` — the cluster version: a SLURM array job
  (`#SBATCH --array=2-101`, one task per CSV data row) that runs the same
  workflow in `production` mode with 16 MPI tasks. It references the CSV and
  `run_dft_workflow.sh` relative to the working directory, so submit it from
  inside `scripts/` on the cluster: `sbatch run_batch_screening.sh`.

Both scripts parse the CSV with Python's `csv` module (several adsorbent
names contain commas) and sanitize names into filesystem-safe case names.
When running DFT, be sure to save the results from all runs — including
failed and intermediate ones — together with their input config files, so
the exact inputs behind any energy number in `scripts/master_results.txt`
can always be recovered.

## DFT Calculation Process

### Quantum Espresso Input Production
An automated pipeline for automatically converting a compound of interest into a Quantum Espresso input is in development, but this is the manual process for the time being. 
1. Using [PubChem](https://pubchem.ncbi.nlm.nih.gov/), find the SMILES format of the compound of interest.
2. Using [OpenBabel](https://openbabel.org/index.html), convert the SMILES string into an MDL Molfile (.mol).
3. Using [VESTA](https://jp-minerals.org/vesta/en/), convert the MDL Molfile into a Crystallographic Information File (.cif), inputting and modifying crystal orientations/morphologies as necessary. 
4. Using [cif2cell](https://pypi.org/project/cif2cell/), convert the Crystallographic Information File into a Quantum Espresso input file (.in).
5. Modify the input file with pseudopotentials for constituent atoms and standard run parameters based on those atoms.
6. Run PWSCF simulation with input file via [Quantum Espresso](https://www.quantum-espresso.org/Doc/INPUT_PW.html) (pw.x). 
Note: This method represents a slight workaround from the typical DFT calculation process designed around VASP POSCAR files, used with VASP instead of Quantum Espresso. It is possible that some information is lost or improperly assumed in this conversion process, particularly at the .mol to .cif file conversion step with VESTA.
`qespresso_pipeline/smiles_qespresso.py` resolves the VESTA binary via the `VESTA_PATH` environment variable first, then via `VESTA` on PATH (`shutil.which`); export `VESTA_PATH=/path/to/VESTA` on machines where VESTA is not on PATH.


The current pipeline automates the full process from SMILES to adsorption energy using Quantum ESPRESSO.

**Steps:**

1. Convert SMILES → `.mol` using Open Babel  
2. Convert `.mol` → `.cif` using pymatgen  
3. Convert `.cif` → QE input (`.in`) using `cif2cell`  
4. Patch QE input with:
   - PBE functional
   - Cutoffs (e.g., `ecutwfc`, `ecutrho`)
   - `K_POINTS = 1 1 1` (molecular system)
   - Pseudopotential paths
5. Run `pw.x` for:
   - Adsorbent
   - PFAS
   - Adsorbent–PFAS complex
6. Parse total energies and compute:
E_ads = E_complex - E_adsorbent - E_PFAS

### Running a Case
```
python qespresso_pipeline/run_adsorption_case.py \
  --case-name <case_name> \
  --adsorbent-name <adsorbent_name> \
  --pfas-name <pfas_name> \
  --adsorbent-smiles "<SMILES>" \
  --pfas-smiles "<SMILES>" \
  --compound-root compounds \
  --workdir dft_cases \
  --pseudo-dir qespresso_pipeline/Pseudopotentials \
  --mode cluster \
  --pw-command "pw.x"
```

#### Pseudopotentials in this repository

The repository ships two distinct pseudopotential sets:

- `qespresso_pipeline/Pseudopotentials/` — the canonical set for the
  automated pipeline: ~94 per-element `.UPF` files (SSSP-style). Every
  example above passes this directory via `--pseudo-dir`, and
  `run_adsorption_case.py` symlinks it into each case directory under
  `dft_cases/`.
- `TFAsim/*.UPF` — a small kjpaw set (C, F, H, N, O plus
  `Fe.pbesol-spn-kjpaw_psl.1.0.0.UPF`) used by the earlier manual iron/TFA
  calculations whose inputs and outputs live in `TFAsim/`.

New automated runs should use the `qespresso_pipeline/Pseudopotentials/` set.

#### Reusing Existing Calculations

You can skip parts of the workflow if outputs already exist:

```
--skip-ads → reuse adsorbent
--skip-pfas → reuse PFAS
--skip-complex → reuse complex
```
- Providing PFAS Energy Directly

#### If PFAS energy is already known:
```
--pfas-energy-ry <value>
```
- Skips PFAS calculation
- Uses provided energy in adsorption calculation

#### Directory Structure
```
/storage/ice-shared/cs8903onl/mussmann-pfas/
├── compounds/
│   ├── adsorbents/
│   │   └── <adsorbent_name>/
│   └── pfas/
│       └── <pfas_name>/
├── dft_cases/
│   └── <case_name>/
│       └── complex/
├── dft_runs/
│   └── <case_name>/
│       ├── job.sbatch
│       ├── meta.json
│       └── outputs/
├── qespresso_pipeline/
├── scripts/
│   └── run_dft_workflow.sh
└── qe_environment.yaml
```

The `qe_environment.yaml` at the repo root is the single canonical copy: the workflow reads it from there, and no duplicate is kept under `scripts/`.

#### Important Scripts

- `run_adsorption_case.py`
Runs a full adsorption-energy case.

It prepares and runs:

Adsorbent
PFAS
Adsorbent–PFAS complex

Then it parses total energies and computes adsorption energy.

*Molecular adsorbent example*

```
python qespresso_pipeline/run_adsorption_case.py \
  --case-name imidazolium_tfa \
  --adsorbent-name imidazolium \
  --pfas-name tfa \
  --adsorbent-source smiles \
  --adsorbent-smiles "ADSORBENT_SMILES_HERE" \
  --pfas-smiles "PFAS_SMILES_HERE" \
  --system-type molecule \
  --mode production \
  --compound-root compounds \
  --workdir dft_cases \
  --pseudo-dir qespresso_pipeline/Pseudopotentials \
  --pw-command "pw.x"
```

*Periodic Adsorbent example*

The below is based on PFOA.

```
python qespresso_pipeline/run_adsorption_case.py \
  --case-name pfoa_go \
  --adsorbent-name go_barker \
  --pfas-name pfoa \
  --adsorbent-source cif \
  --adsorbent-cif qespresso_pipeline/benchmarks/go_barker_like.cif \
  --pfas-smiles "OC(=O)C(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)F" \
  --system-type periodic \
  --mode production \
  --compound-root compounds \
  --workdir dft_cases \
  --pseudo-dir qespresso_pipeline/Pseudopotentials \
  --pw-command "pw.x"
```

- `run_dft_workflow.sh`

Cluster-side workflow script.

This is the entrypoint used by SLURM jobs. It:

loads Anaconda
creates or updates the QE conda environment
reads environment variables from the SLURM job
runs run_adsorption_case.py

Usually, you do not run this manually. It is called by dft_wrapper.py.

- `dft_wrapper.py`

Submits DFT jobs to the cluster.

It:

connects to the cluster
creates a case directory under dft_runs/
writes a SLURM job script
exports required variables
submits the job using sbatch

Example for PFOA on GO:

```
python3 scripts/dft_wrapper.py \
  --user arai304 \
  --cluster login-ice.pace.gatech.edu \
  --case-name pfoa_go_check \
  --adsorbent-name go_barker \
  --adsorbent-source cif \
  --adsorbent-cif /storage/ice-shared/cs8903onl/mussmann-pfas/qespresso_pipeline/benchmarks/go_barker_like.cif \
  --pfas-name pfoa \
  --pfas-smiles "OC(=O)C(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)F" \
  --system-type periodic \
  --mode production \
  --cluster-root /storage/ice-shared/cs8903onl/mussmann-pfas \
  --runs-subdir dft_runs \
  --submit-if-missing \
  --cpus 4 \
  --mem-gb 64 \
  --time 18:00:00
```

The `--workflow-script` flag is optional: by default the wrapper submits
`<cluster-root>/scripts/run_dft_workflow.sh`, matching the layout above.

- `verify_setup.sh`

Checks the local setup in one command — useful right after cloning, or when
something behaves oddly. Run it from anywhere inside the repository:

```
bash scripts/verify_setup.sh
```

It prints one PASS / SKIP / FAIL line per check and exits non-zero when
anything failed. It only inspects the environment and the checkout — it
changes nothing. It checks:

- conda on PATH, and whether the `pfas` environment exists (create it from
  `environment.yaml` if the check fails)
- the DFT toolchain (`pw.x`, `obabel`, `cif2cell`) — found on PATH or in the
  qe environment at `~/.conda/envs/qe_pfas`; on the PACE-ICE login nodes a
  missing `pw.x` is reported as expected (the quantum-espresso module only
  activates inside SLURM jobs)
- optional VESTA (`VESTA_PATH` or on PATH) — skipped when absent
- `scripts/*.sh` line endings — CRLF checkouts of these scripts fail on the
  cluster

- `deploy_to_cluster.sh`

Deploys this checkout to the cluster and verifies the result. It refuses to
run when any `scripts/*.sh` in this checkout has Windows (CRLF) line endings,
and refuses to overwrite a cluster copy that has drifted from this checkout
unless `--force` is given. Run it from the repository root:

```
bash scripts/deploy_to_cluster.sh --user <gatech-user>
```

It wraps the same `rsync --delete` documented under "Deploying to the
Cluster" below, and takes the same `--cluster` and `--cluster-root` flags
with the same defaults as `dft_wrapper.py`.

- `check_cluster_drift.sh`

Compares the cluster copy of this repository against this checkout, printing
a diff and exiting non-zero when they differ — safe to run before submitting
a batch of jobs. It ships itself over SSH, so nothing needs to be installed
on the cluster first:

```
bash scripts/check_cluster_drift.sh --user <gatech-user>
```

Line endings are ignored, and the bulk data / run / cache directories the
deploy excludes are skipped, so a Windows checkout does not show up as
drift.

#### Deploying to the Cluster

The SLURM jobs submitted by `dft_wrapper.py` run `scripts/run_dft_workflow.sh`
from this repository on the cluster, so the copy on the cluster must match this
checkout. Deploy from the repository root on any machine with SSH access to
PACE-ICE — either the plain command:

```
rsync -av --delete \
  --exclude data/ --exclude dft_runs/ --exclude dft_cases/ --exclude compounds/ \
  ./ <user>@login-ice.pace.gatech.edu:/storage/ice-shared/cs8903onl/mussmann-pfas/
```

`run_dft_workflow.sh` locates the repository root on its own (it walks up from
its own location until it finds `qe_environment.yaml`), so it runs correctly
from `scripts/` without copying files to the root or creating symlinks.

Or use the guarded script, which runs the same rsync with two refusals in
front of it (CRLF shell scripts in this checkout; a cluster copy with
hand-edits, overridable with `--force`) and re-checks the deployed copy
afterwards:

```
bash scripts/deploy_to_cluster.sh --user <gatech-user>
```

To compare the two copies without deploying anything, run
`scripts/check_cluster_drift.sh` with the same `--user` flag; it prints a
diff and exits non-zero on drift.

#### Explicit Slurm Scheduling on PACE-ICE

The jobs submitted by `dft_wrapper.py` do not pin any scheduling attributes
unless you ask for them: with no flags, each job lands wherever the cluster's
default partition, account, and QOS point on the day it is submitted. When
submitting on PACE-ICE, pass the scheduling attributes explicitly so jobs are
not routed by whatever the cluster default happens to be that day:

```
python3 scripts/dft_wrapper.py \
  ... \
  --partition ice-cpu \
  --account coc \
  --qos coc-ice
```

`--partition`, `--account`, and `--qos` are all optional; omitting them
reproduces the previous behavior exactly (no `#SBATCH` lines are added to the
generated job script). The values above (`partition=ice-cpu`, `account=coc`,
`qos=coc-ice`) are the PACE-ICE values verified during the May 2026 audit —
adjust them if the cluster re-allocates resources. The batch screening array
job (`scripts/run_batch_screening.sh`) carries the same values in its static
`#SBATCH` block.

Note that the `quantum-espresso` module can only be loaded and run inside
compute-node jobs: on a login node, `module load quantum-espresso` appears to
succeed, but `pw.x` is unavailable (the module is guarded by Lmod so that it
only activates within jobs). Run the workflows via `sbatch` from
`dft_wrapper.py` or the array script, not directly on the login node. When
loading the module anywhere (workflow script or manual run), pin the native
build explicitly — `module load quantum-espresso/7.3`; the note under Manual
DFT Simulation below explains why the unpinned default is unreliable.

#### Memory sizing (`--mem-gb`)

`pw.x` memory grows with the simulation cell. The wrapper now defaults to
`--mem-gb 64` and `--time 18:00:00` (8 CPUs), and the array script requests
`--ntasks=4` with `--mem=64G` — enough for typical screening cases, including
the ~30 Å cells produced by long alkyl chains; the walltime matches the 18 h
cap on the `ice-cpu` partition. Size the request to the largest cell the case
will build:

| Case | Typical largest cell dimension | Suggested `--mem-gb` |
|---|---|---|
| Small adsorbents / short molecules (e.g. TFA pairs) | up to ~20 Å | 32 |
| Large adsorbents or long alkyl chains | ~30 Å | 64 (the default) |
| Adsorbent–PFAS complex of a large pair | larger than the adsorbent cell | 96 if the 64 default is OOM-killed |

After a run, `seff <jobid>` shows the memory actually used. If `pw.x` exits
with return code 137 or the epilog reports `oom_kill`, the job ran out of
memory: resubmit with a higher `--mem-gb` rather than assuming a code failure.

At submit time `dft_wrapper.py` also prints a `[WARN]` line when the requested
`--mem-gb` looks small for the SMILES given (a rough heavy-atom count over
what the job will actually run, honoring the skip flags and
`--pfas-energy-ry`). It is advisory only — submission proceeds — and it
cannot see CIF-sourced adsorbents or the final simulation cell, so the table
and `seff` remain the authority.

### Manual DFT Simulation

For tuning purposes, it will likely be necessary to manually create a DFT input file from a CIF file, created either via ase, pymatgen, or sourced from a crystallographic database. You begin by running a command of this following structure to create an input file:

```
cif2cell [input_file].cif -p quantum-espresso -o [output_file].in
```

This gives you a generic input file. Depending on if you have a molecular or periodic/metallic structure, we then need to append a certain number of things. For molecular, the structure may look something like this:

```
&CONTROL
  calculation='relax', ! to denote the type of calculation you want to run
  outdir='./Outputs', ! where it sends metadata, run results, etc.
  prefix='tfanoh_isolated', ! title of results
  pseudo_dir='./Pseudopotentials', ! where it sources pseudopotential files for the run
  verbosity='low',
  tprnfor=.true., ! additional force/stress calculations
  tstress=.true.,
  forc_conv_thr=7.7D-4, ! global convergence thresholds
  etot_conv_thr=7.3D-7
/
&SYSTEM
  ibrav = 1
  A = 15.0  ! for molecules, you need vacuum on the sides to prevent spurious interactions
  nat = 7 ! that means rescaling the ATOMIC_POSITIONS parameters
  ntyp = 3
  tot_charge = -1.0 ! specifying the ionic nature 
  assume_isolated = 'martyna-tuckerman' ! this is a correction factor for isolated molecular systems
  ecutwfc=60, ! energy cutoffs, the higher the more accurate, but the more memory/time required
  ecutrho=480,
  input_dft='pbe',
  occupations='fixed',
  vdw_corr='grimme-d3' ! van der waals force correction
/
&ELECTRONS
  conv_thr=1d-07, ! self-consistent convergence threshold
  mixing_beta=0.3, ! mixing factor (akin to a learning rate, too high is unstable, too low is slow)
/
&IONS
  ion_dynamics='bfgs', ! relaxation dynamics for ions
/
CELL_PARAMETERS {angstrom}
  15.00000000000000   0.000000000000000   0.000000000000000 
  0.000000000000000   15.00000000000000   0.000000000000000 
  0.000000000000000   0.000000000000000   15.00000000000000 
ATOMIC_SPECIES
   F   18.99800   F.UPF ! PAW pseudopotential files in the given directory
   O   15.99900   O.UPF
   C   12.01060   C.UPF
ATOMIC_POSITIONS {angstrom}
F   8.071326   5.984002   7.794029 ! note that these parameters are clearly bounded by vacuum
F   6.411133   7.035002   8.740828 ! note that the cell goes from [0,15], and this is centred
F   6.351331   6.595001   6.598528
O   7.353828   9.388298   7.092531
O   9.255626   8.192599   7.341530
C   7.891528   8.311999   7.321427
C   7.165231   6.993100   7.611128
K_POINTS gamma ! equivalent to a 1 1 1 grid, appropriate for isolated systems
```

Conversely, if you have a periodic/metallic structure, you will need some additions, such as:
```
&CONTROL ! these only describe additions that must be made
  tefield=.true., ! electric field potential for lattices
  dipfield=.true., ! dipole correction factor for lattices
  max_seconds=40000, ! periodic structures run longer, so this sets a save point for restarts
&SYSTEM
  occupations='smearing', ! Gaussian smoothing filter, necessary for metals
  smearing='mv', ! specific form of cold smearing
  degauss=0.01,
  nspin=2, ! spin-polarization, due to magnetism of system
  edir=3, ! electric field/dipole correction axis
  emaxpos=0.9, ! maximum electric potential, placed in vacuum above all atoms
  eopreg=0.1, ! zone of decline for dipole factor, placed in vacuum under all atoms
  starting_magnetization(1)=0.3 ! initial magnetization for metal (in this case iron)
&ELECTRONS
  mixing_mode='local-TF', ! inhomogeniety correction for adsorbants
  electron_maxstep=200 ! extending calculation steps to promote convergence
/
&IONS
  wfc_extrapolation='second_order', ! wave function and potential optimizers
  pot_extrapolation='second_order', ! increases runtime drastically, trading small portion of accuracy
/
ATOMIC_POSITIONS {crystal} ! note the additional numbers added after the positions
Fe   0.833333333333333   0.333333333333333   0.355649309796759 0 0 0 ! 0s represent fixed structure 
 C   0.523961155391464   0.283367120111382   0.752928148328609 1 1 1 ! 1s represent free movement
 K_POINTS {automatic} ! we need fixed layers to simulate bulk behaviour 
  5 5 1 0 0 0 ! periodic structures need K-mesh for accurate energy calc, 5x5x1 is good for slabs
```

(Note here that, for the periodicity to be preserved, we may need a charge correction using trifluoroacetic acid as opposed to trifluoroacetate.)

These files can be run on PACE ICE with parallelization as follows:
```
module load quantum-espresso/7.3
module load openmpi
mpirun -np [number_of_processors] pw.x -in [input_file].in > [output_file].out
```

Pin the Quantum ESPRESSO build explicitly (`quantum-espresso/7.3`): the
cluster's unpinned default can resolve to a container-based build whose `pw.x`
fails under `mpirun`, and loading `openmpi` afterwards can silently swap the
active build. 7.3 is the native build the repository workflows are verified
against on PACE-ICE; `scripts/run_dft_workflow.sh` pins the same version.

## Machine Learning in This Repository

Besides the DFT pipeline, the repository carries three independent machine
learning efforts. They are research and exploration code, separate from the
DFT pipeline.

### Fast tree-based screening models (`ml/`)

`ml/fast_tree_based_training_demo.py` trains quick tabular regressors on a
candidate table produced by the data pipeline:

```
python3 ml/fast_tree_based_training_demo.py --in data/quantum_espress_placeholder.csv --model hgb
```

Three model families are supported (`--model hgb | extratrees | rf`:
HistGradientBoosting, ExtraTrees, RandomForest). The script does a stratified
train/test split, a small randomized hyperparameter search on the training
set only, and writes `models/fast_tree_<model>.joblib` plus
`models/fast_tree_<model>_metrics.json`. The committed `models/` files are
the metrics JSONs and captured console logs (`*_out.txt`, including
top-feature importances) from example runs; the `.joblib` model files are
gitignored.

### Basic molecule GNN playground (`basic_molecule_gnn/`)

`basic_molecule_gnn/basic_gnn_molecule.py` is a self-contained introduction
to molecular graph neural networks: it loads the ESOL dataset from
MoleculeNet and trains small GCN and GAT models for property regression. It
runs in the repository's third conda environment (`pfas_gnn_env`, see the
table at the top).

### Early scikit-learn baselines (`shivani_ml_models/`)

`shivani_ml_models/ml_model.py` trains baseline scikit-learn regressors
(support-vector, random forest, gradient boosting) on
`data/pubchem_properties.csv` (target `MolecularWeight`); `gnn.py` is a
minimal PyTorch-Geometric GCN on the ENZYMES dataset, and `ml_model.ipynb`
is the accompanying notebook.

### Feature documentation and team ops

- `docs/ml_features.md` documents every feature used by the tabular models
  (PubChem descriptors, RDKit structure flags, PFAS identity features) with
  the literature rationale for each.
- `mgmt_ops.md` is the team's project-operations note (roles, meetings,
  reporting cadence) — not needed to run any code.

## PFAS candidate dashboard

Run `uv run --no-default-groups --group web --group preparation python -m dashboard` for the React/FastAPI dashboard of
all clusters. See [dashboard setup, chemistry, QE, and snapshot publishing](docs/dashboard.md).

The dashboard also supports selection from filtered tiles/rows, bulk preparation
and QE batches, persistent queue controls, and optional Apptainer memory isolation
with bounded parallel container jobs. See [dashboard setup and queue documentation](docs/dashboard.md#selected-batches-and-job-queue).
