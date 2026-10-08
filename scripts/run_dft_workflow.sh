#!/usr/bin/env bash
set -euo pipefail

echo "[job] starting at $(date)"
echo "[job] host: $(hostname)"
echo "[job] pwd: $(pwd)"

if command -v module &> /dev/null; then
    # Pin Quantum ESPRESSO to the native 7.3 build verified on PACE-ICE.
    # The unpinned default can resolve to a container-based build whose
    # pw.x fails under "mpirun -np ... pw.x", and loading openmpi after
    # an unpinned QE load can silently swap in a different build.
    module purge
    module load anaconda3
    module load gcc/12.3.0
    module load openmpi/4.1.5
    module load quantum-espresso/7.3
fi

if command -v conda &> /dev/null; then
    eval "$(conda shell.bash hook)"
else
    echo "[error] conda command not found. Please ensure Anaconda/Miniconda is installed locally and in your PATH."
    exit 1
fi

# Locate the repo root. The script may live in scripts/ (repo checkout), at
# the repo root (older deployments), or be invoked through a symlink at the
# repo root, so resolve the physical script path and walk up instead of
# assuming a fixed relative position. Anchor the walk-up on the root-only
# qespresso_pipeline/ directory; it exists only at the repo root, so it
# reliably marks the project root.
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_ROOT="$(dirname "$SCRIPT_PATH")"
while [[ "$PROJECT_ROOT" != "/" && ! -d "$PROJECT_ROOT/qespresso_pipeline" ]]; do
    PROJECT_ROOT="$(dirname "$PROJECT_ROOT")"
done
if [[ ! -f "$PROJECT_ROOT/qe_environment.yaml" ]]; then
    echo "[error] qe_environment.yaml not found at $SCRIPT_PATH or any parent directory."
    echo "[error] Deploy the full repository (see README) and run this script from within it."
    exit 1
fi
cd "$PROJECT_ROOT"

ENV_YAML="$PROJECT_ROOT/qe_environment.yaml"
ENV_PREFIX="${HOME}/.conda/envs/qe_pfas"

if [[ ! -f "$ENV_YAML" ]]; then
    echo "[error] Missing environment file: $ENV_YAML"
    exit 1
fi

if [[ ! -d "$ENV_PREFIX" ]]; then
    echo "[env] creating env at $ENV_PREFIX from $ENV_YAML"
    conda env create -p "$ENV_PREFIX" -f "$ENV_YAML"
else
    conda activate qe_pfas
fi

echo "[env] python:"
conda run -p "$ENV_PREFIX" python --version
conda run -p "$ENV_PREFIX" python -c "import sys; print(sys.executable)"

export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
MPI_TASKS="${SLURM_NTASKS:-1}"

CASE_NAME="${CASE_NAME:?CASE_NAME not set}"
ADSORBENT_NAME="${ADSORBENT_NAME:?ADSORBENT_NAME not set}"
PFAS_NAME="${PFAS_NAME:?PFAS_NAME not set}"

ADSORBENT_SOURCE="${ADSORBENT_SOURCE:-smiles}"
SYSTEM_TYPE="${SYSTEM_TYPE:-molecule}"
MODE="${MODE:-cluster}"

ADSORBENT_SMILES="${ADSORBENT_SMILES:-}"
ADSORBENT_CIF="${ADSORBENT_CIF:-}"
PFAS_SMILES="${PFAS_SMILES:-}"
PFAS_ENERGY_RY="${PFAS_ENERGY_RY:-}"

PSEUDO_DIR="${PSEUDO_DIR:-$PROJECT_ROOT/qespresso_pipeline/Pseudopotentials}"
PW_COMMAND="${PW_COMMAND:-mpirun -np $MPI_TASKS pw.x}"
COMPOUND_ROOT="${COMPOUND_ROOT:-$PROJECT_ROOT/compounds}"
WORKDIR="${WORKDIR:-$PROJECT_ROOT/dft_cases}"

ARGS=(
  --case-name "$CASE_NAME"
  --adsorbent-name "$ADSORBENT_NAME"
  --pfas-name "$PFAS_NAME"
  --adsorbent-source "$ADSORBENT_SOURCE"
  --system-type "$SYSTEM_TYPE"
  --mode "$MODE"
  --compound-root "$COMPOUND_ROOT"
  --workdir "$WORKDIR"
  --pseudo-dir "$PSEUDO_DIR"
  --pw-command "$PW_COMMAND"
)

if [[ -n "$ADSORBENT_SMILES" ]]; then
  ARGS+=(--adsorbent-smiles "$ADSORBENT_SMILES")
fi
if [[ -n "$ADSORBENT_CIF" ]]; then
  ARGS+=(--adsorbent-cif "$ADSORBENT_CIF")
fi
if [[ -n "$PFAS_SMILES" ]]; then
  ARGS+=(--pfas-smiles "$PFAS_SMILES")
fi
if [[ -n "$PFAS_ENERGY_RY" ]]; then
  ARGS+=(--pfas-energy-ry "$PFAS_ENERGY_RY")
fi

if [[ "${SKIP_ADS:-0}" == "1" ]]; then ARGS+=(--skip-ads); fi
if [[ "${SKIP_PFAS:-0}" == "1" ]]; then ARGS+=(--skip-pfas); fi
if [[ "${SKIP_COMPLEX:-0}" == "1" ]]; then ARGS+=(--skip-complex); fi

conda run -p "$ENV_PREFIX" python qespresso_pipeline/run_adsorption_case.py "${ARGS[@]}"

echo "[job] finished successfully at $(date)"
