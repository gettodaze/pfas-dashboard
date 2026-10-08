---
theme: default
title: DFT Workflow Helpers
author: John McCloskey
info: |
  Eight demonstrated helpers and one comparison with existing automation.
  Branch: mccloskey/run_simulations_20261001 through c31f829.
layout: default
aspectRatio: 16/9
colorSchema: light
routerMode: hash
fonts:
  sans: Arial
  mono: monospace
  provider: none
defaults:
  layout: default
---

<div class="title-page">

# DFT Workflow Helpers

John McCloskey · October 1, 2026

</div>

<!--
"John has added short shell commands for his own day-to-day workflow: selecting
candidates, launching calculations, and inspecting jobs and output files."
-->

---

# Outline

1. **Selection:** choose a candidate, submit TFA, and reuse workflow scripts.
2. **Inspection:** check jobs, resources, processes, and output-file sizes.

<!--
"We will start with candidate selection and submission, show how these helpers
fit around existing scripts, then check jobs and output-file sizes."
-->

---

# Selection: pf_shortest_smiles

<div class="columns">
<div>
<div class="panel-label">Function</div>

```bash
pf_shortest_smiles() {
  mlr --csv put \
    '$len = strlen($medoid_SMILES)' \
    then sort -n len \
    shivani_ml_models/cluster_centers.csv
}
```

<div class="annotation">Add a length column, then sort smallest first.</div>
</div>
<div>
<div class="panel-label">Output excerpt</div>

```text
$ pf_shortest_smiles | head
cluster  n_points  medoid_SMILES  len
553      57        C(NC(=S)N)O    11
809      65        CC1C(O1)C=C    11
58       8         C=CC(=O)[O-]   12
147      74        C1CC1N=C(N)N   12
```

<div class="annotation">Cluster 553: medoid SMILES length 11.</div>
</div>
</div>


<!--
"John is improving his own ergonomics for running the existing
workflow. This helper puts shorter candidate structures first, so it is easy
to pick a starting case. Here that is cluster 553."
SMILES length is a selection heuristic, not a runtime prediction.
Selected columns and rows retain the supplied values.
-->

---

# Selection: pf_extract_cluster

<div class="columns">
<div>
<div class="panel-label">Function excerpt</div>

```bash
pf_extract_cluster() {
  local line_no=$(( $1 + 2 ))
  # ... read that row into PF_LINE ...
  export PF_CLUSTER_NO="$(
    printf '%s\n' "$PF_LINE" |
      pf_print_column_csv 1)"
  export PF_CLUSTER_SMILE="$(
    printf '%s\n' "$PF_LINE" |
      pf_print_column_csv 12)"
}
```

</div>
<div>
<div class="panel-label">Output excerpt</div>

```text
$ pf_extract_cluster 553
PF_CLUSTER_NO: 553
PF_CLUSTER_SMILE: 'C(NC(=S)N)O'
```

<div class="annotation">Column 1 → cluster ID.<br>Column 12 → medoid SMILES.</div>
<div class="annotation">+2 accounts for the header and one-based line numbers.</div>
</div>
</div>


<!--
"The next helper remembers the selected candidate's ID and
structure. John can reuse them in the next command instead of copying a long
CSV row by hand."
pf_print_line uses awk NR == n; pf_print_column_csv uses awk -F','. This is a
row-index lookup, not a search for a cluster ID, and assumes the CSV layout.
-->

---

# Selection: pf_dft_wrapper_cluster_tfa

<div class="columns">
<div>
<div class="panel-label">Function excerpt</div>

```bash
pf_dft_wrapper_cluster_tfa() {
  # ... check variables; print preview ...
  read -rp "Run? [y/N] " x
  [[ "$x" =~ ^[Yy]$ ]] && \
    python scripts/dft_wrapper.py \
    --adsorbent-name "c$PF_CLUSTER_NO" \
    --case-name "jmccloskey30-c$PF_CLUSTER_NO" \
    --adsorbent-smiles "$PF_CLUSTER_SMILE"
  # Fixed arguments omitted above:
  # user, TFA, source, and cluster paths.
}
```

<div class="annotation">Use the selected cluster ID and SMILES.</div>
</div>
<div>
<div class="panel-label">Output excerpt</div>

```text
$ pf_dft_wrapper_cluster_tfa
About to run: python scripts/dft_wrapper.py
  ...
  --pfas-name tfa
  --pfas-smiles 'FC(F)(F)C(=O)O'
  --adsorbent-name 'c553'
  --case-name 'jmccloskey30-c553'
  --adsorbent-smiles 'C(NC(=S)N)O'
Run? [y/N]
```

<div class="annotation">Preview first; y or Y confirms.</div>
</div>
</div>


<!--
"This fills in the candidate and TFA inputs, then shows the command
before running it. It saves typing and gives John a quick check before saying
yes. The following slides show his running jobs."
This explanatory excerpt omits fixed arguments and is not a replacement for
the real function. The full version passes --submit-if-missing and fixed
user/source/root/workflow/PFAS options. This capture does not show a response
or job receipt, so it alone does not prove submission.
-->

---

# Selection: Existing workflow scripts

<div class="columns">
<div>
<div class="panel-label">run_batch_screening.sh · excerpt</div>

```bash
#SBATCH --array=2-26
# ... parse one CSV row per task ...
export CASE_NAME="${ADS_NAME}_TFA"
export ADSORBENT_NAME="$ADS_NAME"
export ADSORBENT_SMILES="$ADS_SMILES"
export PFAS_NAME="TFA"
export PFAS_SMILES="FC(F)(F)C(=O)O"
bash run_dft_workflow.sh
```

<div class="annotation">CSV row → named TFA case → workflow.</div>
</div>
<div>
<div class="panel-label">run_dft_workflow.sh · excerpt</div>

```bash
MPI_TASKS="${SLURM_NTASKS:-1}"
# ... configure MPI and build ARGS ...
conda run -p "$ENV_PREFIX" python \
  qespresso_pipeline/run_adsorption_case.py \
  "${ARGS[@]}"
```

<div class="annotation">Environment + MPI → calculation pipeline.</div>
</div>
</div>


<div class="annotation">Existing scripts handle execution; these helpers make my workflow easier to run.</div>

<!--
"Some of this repeats existing tooling. Batch screening already
selects candidates, and the workflow script runs the calculations. John's
focus is his own ergonomics: less typing, easier checks, and better visibility
into jobs and storage. These helpers sit around the existing workflow."
Batch input is simple_adsorbents.csv, not the medoid CSV, so candidate libraries
differ. The array has 25 tasks, each requesting 4 MPI tasks and 192G memory;
that is RAM, not a disk-storage allowance. Actual directives take precedence
over stale comments mentioning 100 rows and 64G. It uses Python csv.reader,
which is more robust than the aliases' awk comma splitting. The aliases also
call the existing dft_wrapper.py and shared workflow rather than implementing
the DFT pipeline again. No claim that these scripts already inspect file sizes.
-->

---

# Inspection: pf_slurm_squeue

<div class="panel-label">Alias</div>

```bash
alias pf_slurm_squeue="squeue -u $USER"
```

<div class="panel-label">Output</div>

```text
$ pf_slurm_squeue
  JOBID PARTITION     NAME     USER ST    TIME NODES NODELIST(REASON)
6029069   coc-cpu dft_jmcc jmcclosk  R 8:27:33     1 atl1-1-02-004-15-2
6029068   coc-cpu dft_jmcc jmcclosk  R 8:27:47     1 atl1-1-02-003-19-1
```


<div class="annotation">R = running; one node per job.</div>

<!--
"One short command shows John's jobs. Both are running on one
node each, for about eight and a half hours."
Historical user-supplied snapshot, not a fresh cluster query.
-->

---

# Inspection: pf_slurm_running_ids

<div class="panel-label">Alias</div>

```bash
alias pf_slurm_running_ids='pf_slurm_squeue -t RUNNING -h -o "%A"'
```

<div class="columns">
<div>
<div class="annotation">RUNNING filters jobs.<br>-h hides the header.<br>%A prints job IDs.</div>
</div>
<div>
<div class="panel-label">Output</div>

```text
$ pf_slurm_running_ids
6029069
6029068
```

</div>
</div>


<!--
"This returns just the running job numbers. Other helpers can use
that list automatically, saving another copy-and-paste step."
-->

---

# Inspection: pf_slurm_sstat

<div class="panel-label">Function excerpt</div>

```bash
pf_slurm_sstat() {
  pf_slurm_sstat_all --parsable2 \
    --format=JobID,AveCPU,MaxRSS,AveRSS,MaxDiskRead,MaxDiskWrite |
    # ... awk converts RSS and disk counters to GiB ...
    column -t -s'|'
}
```

<div class="panel-label">Output</div>

```text
$ pf_slurm_sstat
JobID          AveCPU    MaxRSS_GiB  AveRSS_GiB  DiskRead_GiB  DiskWrite_GiB
6029069.batch  08:25:18  11.40       2.95        0.13          3.50
6029068.batch  08:25:50  23.31       6.23        0.13          1.91
```


<div class="annotation">Maximum RSS: 11.40 GiB and 23.31 GiB.</div>

<!--
"This makes memory and disk activity easier to read. The second
job reports about twice the maximum memory use. John can quickly see which
run deserves closer inspection."
pf_slurm_sstat_all appends .batch to IDs, joins with commas, and calls sstat.
The omitted awk strips K from RSS and divides by 1048576, divides disk counters
by 1073741824, relabels headers, and formats two decimal places. This excerpt
is not runnable. AveCPU is CPU time, not elapsed time; MaxRSS is not allocation
memory. Disk columns are maximum task counters, not summed job I/O.
-->

---

# Inspection: pf_slurm_processes

<div class="panel-label">Function excerpt</div>

```bash
pf_slurm_processes() {
  # ... find running jobs; srun once per node ...
  listing=$(scontrol listpids "$1")
  # ... extract job PIDs into "$pids" ...
  ps -ww -p "$pids" \
    -o pid,ppid,stat,etime,time,pcpu,pmem,rss,args --sort=-rss
}
```

<div class="panel-label">Output excerpt</div>

```text
$ pf_slurm_processes
Job 6029069
0: Node: atl1-1-02-004-15-2.pace.gatech.edu
0: ELAPSED   TIME     %CPU  RSS      COMMAND
0: 08:28:06  08:25:48 99.5  2207500  pw.x -in adsorbent.in
```


<div class="annotation">pw.x is running adsorbent.in at 99.5% CPU.</div>

<!--
"This shows the actual calculation inside the job. The adsorbent
calculation is still using about one CPU. It is a useful activity check;
John still needs the calculation output to confirm a finished result."
The excerpt shows the original inner commands without their outer context.
Original srun uses --overlap --exact --immediate=10 with one task/CPU per node
and --label. Output retains exact supplied values for selected fields, omitting
PID, PPID, STAT, %MEM, other processes, and Slurm prolog lines. Parent command
shows jmccloskey30-c553, mpirun -np 1, and --skip-pfas. Not runnable as shown.
-->

---

# Inspection: pf_largest_files

<div class="columns">
<div>
<div class="panel-label">Function</div>

```bash
pf_largest_files() {
  find . -type f -printf '%s %p\n' |
    sort -nr |
    head -n 30 |
    numfmt --field=1 --to=iec
}
```

<div class="annotation">Sort files by bytes; show the largest 30 with readable sizes.</div>
</div>
<div>
<div class="panel-label">Output excerpt</div>

```text
# From test_runs
$ pf_largest_files
177M .../c100/.../charge-density.hdf5
88M  .../c553/.../charge-density.hdf5
72M  .../tfa/.../charge-density.hdf5
34M  .../tfa/.../wfc1.hdf5
```

<div class="annotation">Charge-density and wavefunction files dominate this listing.</div>
</div>
</div>


<div class="annotation">Batch runs can run out of disk space. Check output-file sizes.</div>

<!--
"The biggest files here are calculation artifacts.
Checking output sizes matters: many batch cases multiply these artifacts and
can run out of space. We already have a disk-quota failure in a sample log."
Full paths are under compounds/adsorbents/c100/Outputs/adsorbent.save,
compounds/adsorbents/c553/Outputs/adsorbent.save, and
compounds/pfas/tfa/Outputs/pfas.save. First four supplied entries shown.
Source of quota failure: scripts/sample_logs/simple_screen_5898218_9.out.
Largest files alone are not a quota or total-storage measurement.
Do not infer that c100 belongs to job 6029068 from these separate logs.
-->
