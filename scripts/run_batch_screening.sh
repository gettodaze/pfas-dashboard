#!/bin/bash
#SBATCH --job-name=pfas_screen
#SBATCH --output=logs/simple_screen_%A_%a.out
#SBATCH --nodes=1
#SBATCH --ntasks=4
#SBATCH --time=18:00:00
#SBATCH --mem=192G
#SBATCH --array=2-26  # one task per CSV data row: 101 = 1 header + 100 data rows (IDs 001-100); update if the CSV changes
# Explicit PACE-ICE scheduling values verified on the cluster (account coc,
# qos coc-ice, partition ice-cpu). Each array task runs one adsorption case
# with a single pw.x at a time, so 4 tasks and 64 GB match the per-case
# defaults of dft_wrapper.py (see the memory table in the README), and the
# partition caps walltime at 18 hours. Adjust if the cluster re-allocates
# resources.
#SBATCH --partition=ice-cpu
#SBATCH --account=coc
#SBATCH --qos=coc-ice

CSV_FILE="simple_adsorbents.csv"

# CSV header: ID,Name,SMILES,Category. Parse with python's csv module because
# several fields are quoted and contain commas (e.g. row 045's Name), which
# breaks naive awk -F',' splitting. SLURM_ARRAY_TASK_ID is the 1-based CSV
# line number; line 1 is the header.
PARSED=$(python3 -c '
import csv, sys
with open(sys.argv[1], newline="") as f:
    rows = list(csv.reader(f))
idx = int(sys.argv[2]) - 1
if idx < 1 or idx >= len(rows):
    sys.exit(f"no data row at CSV line {sys.argv[2]}")
sys.stdout.write(rows[idx][1] + "\t" + rows[idx][2])
' "$CSV_FILE" "$SLURM_ARRAY_TASK_ID") || exit 1
ADS_NAME=${PARSED%%$'\t'*}
ADS_SMILES=${PARSED#*$'\t'}

if [[ -z "$ADS_NAME" || -z "$ADS_SMILES" ]]; then
    echo "[error] could not extract Name/SMILES from $CSV_FILE line $SLURM_ARRAY_TASK_ID" >&2
    exit 1
fi

# Filesystem-safe adsorbent name: it becomes part of the case directory and
# compounds/adsorbents/ path, and several Names contain spaces, slashes and
# commas (e.g. "Crown Ether / 18-Crown-6").
ADS_NAME=$(printf '%s' "$ADS_NAME" | tr -c 'A-Za-z0-9._-' '_' | sed -E 's/_+/_/g; s/^_+//; s/_+$//')

export CASE_NAME="${ADS_NAME}_TFA"
export ADSORBENT_NAME="$ADS_NAME"
export ADSORBENT_SMILES="$ADS_SMILES"
export PFAS_NAME="TFA"
export PFAS_SMILES="FC(F)(F)C(=O)O" 
export MODE="production"
export SYSTEM_TYPE="molecule"
export MPI_TASKS=$SLURM_NTASKS

bash run_dft_workflow.sh