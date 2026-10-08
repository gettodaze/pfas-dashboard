# Remember the location when sourced so slide exports work from any directory.
PF_ALIASES_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

pf_print_line() {
    awk -v n="$1" 'NR == n'
}

pf_print_column_csv() {
    awk -F',' -v col="$1" '{print $col}'
}

pf_extract_cluster() {
    local line_no=$(( $1 + 2 ))

    export PF_LINE="$(
        pf_print_line "$line_no" < shivani_ml_models/cluster_centers.csv
    )"
    echo "PF_LINE: $PF_LINE"

    export PF_CLUSTER_NO="$(
        printf '%s\n' "$PF_LINE" | pf_print_column_csv 1
    )"
    echo "PF_CLUSTER_NO: $PF_CLUSTER_NO"

    export PF_CLUSTER_SMILE="$(
        printf '%s\n' "$PF_LINE" | pf_print_column_csv 12
    )"
    echo "PF_CLUSTER_SMILE: '$PF_CLUSTER_SMILE'"
}

pf_dft_wrapper_cluster_tfa() {
    [[ -z "${PF_CLUSTER_NO:-}" || -z "${PF_CLUSTER_SMILE:-}" ]] && { echo "Cluster vars not set"; return 1; }

    echo "About to run: python scripts/dft_wrapper.py --user jmccloskey30 --pfas-name tfa --pfas-smiles 'FC(F)(F)C(=O)O' --adsorbent-source smiles --submit-if-missing --cluster-root /home/hice1/jmccloskey30/test_runs --workflow-script /home/hice1/jmccloskey30/pfas-environment-cleanup/scripts/run_dft_workflow.sh --adsorbent-name 'c$PF_CLUSTER_NO' --case-name 'jmccloskey30-c$PF_CLUSTER_NO' --adsorbent-smiles '$PF_CLUSTER_SMILE'"

    read -rp "Run? [y/N] " x
    [[ "$x" =~ ^[Yy]$ ]] && python scripts/dft_wrapper.py --user jmccloskey30 --pfas-name tfa --pfas-smiles 'FC(F)(F)C(=O)O' --adsorbent-source smiles --submit-if-missing --cluster-root /home/hice1/jmccloskey30/test_runs --workflow-script /home/hice1/jmccloskey30/pfas-environment-cleanup/scripts/run_dft_workflow.sh --adsorbent-name "c$PF_CLUSTER_NO" --case-name "jmccloskey30-c$PF_CLUSTER_NO" --adsorbent-smiles "$PF_CLUSTER_SMILE"
}

pf_slurm_update_email() {
    scontrol update JobId="${1:?Usage: pf_update_email JOBID}" \
        MailUser=jmccloskey30@gatech.edu MailType=END,FAIL
}

pf_shortest_smiles() {
    mlr --csv put '$len = strlen($medoid_SMILES)' then sort -n len shivani_ml_models/cluster_centers.csv
}

alias pf_slurm_squeue="squeue -u $USER"
alias pf_slurm_running_ids='pf_slurm_squeue -t RUNNING -h -o "%A"'
pf_slurm_sstat_all() {
    local jobs
    jobs="$(pf_slurm_running_ids | sed 's/$/.batch/' | paste -sd, -)"
    [[ -n "$jobs" ]] && sstat -j "$jobs" "$@"
}
pf_slurm_sstat() {
    pf_slurm_sstat_all --parsable2 --format=JobID,AveCPU,MaxRSS,AveRSS,MaxDiskRead,MaxDiskWrite |
    awk -F'|' 'BEGIN{OFS="|"}
        NR==1 {$3="MaxRSS_GiB"; $4="AveRSS_GiB"; $5="DiskRead_GiB"; $6="DiskWrite_GiB"}
        NR>1 {
            gsub(/K/,"",$3); gsub(/K/,"",$4)
            $3=sprintf("%.2f",$3/1048576)
            $4=sprintf("%.2f",$4/1048576)
            $5=sprintf("%.2f",$5/1073741824)
            $6=sprintf("%.2f",$6/1073741824)
        } 1' |
    column -t -s'|'
}
alias pf_slurm_recent_ids="ls -1r ~/outputs | sed -n 's/^slurm-\([0-9]\+\)\..*/\1/p' | uniq | head"
pf_slurm_recent_logs() {
    local id
    id="$(pf_slurm_recent_ids | head -n 1)"
    code ~/outputs/slurm-"$id".out ~/outputs/slurm-"$id".err
}

pf_slurm_srun_bash() {
    srun --jobid=${1:?Usage: pf_update_email JOBID} --overlap --pty bash
}

# Show only the selected job's processes, once on each allocated node.
pf_slurm_processes() {
    local jobs job_id node_count
    jobs="$(squeue -u "$USER" -t RUNNING -h -o '%i %D')" || return
    [[ -n "$jobs" ]] || { echo "No running jobs."; return 0; }

    while read -r job_id node_count; do
        printf '\nJob %s\n' "$job_id"
        srun --jobid="$job_id" --overlap --exact --immediate=10 \
            --nodes="$node_count" --ntasks="$node_count" \
            --ntasks-per-node=1 --cpus-per-task=1 --label \
            bash -c '
                printf "Node: %s\n" "$(hostname)"
                listing=$(scontrol listpids "$1") || exit
                pids=$(printf "%s\n" "$listing" |
                    awk '\''$1 ~ /^[0-9]+$/ {print $1}'\'' | paste -sd, -)
                if [[ -n "$pids" ]]; then
                    ps -ww -p "$pids" \
                        -o pid,ppid,stat,etime,time,pcpu,pmem,rss,args --sort=-rss
                else
                    echo "No job processes found."
                fi
            ' bash "$job_id" || printf 'Could not inspect job %s.\n' "$job_id" >&2
    done <<< "$jobs"
}

pf_largest_files() {
    find . -type f -printf '%s %p\n' |
    sort -nr |
    head -n 30 |
    numfmt --field=1 --to=iec
}

# Export Slidev Markdown; default output is beside the input with a .pptx suffix.
pf_md_to_pptx() {
    if [[ $# -eq 0 ]]; then
        echo "Usage: pf_md_to_pptx INPUT.md [OUTPUT.pptx] [Slidev options]" >&2
        return 1
    fi

    local input output cli node_bin browser
    local -a browser_args=()
    input="$(realpath -e -- "$1")" || return
    [[ -f "$input" ]] || { echo "Not a file: $input" >&2; return 1; }
    shift
    output="${input%.*}.pptx"
    if [[ $# -gt 0 && "$1" != --* ]]; then
        output="$1"
        shift
    fi

    cli="$PF_ALIASES_DIR/slides/node_modules/.bin/slidev"
    node_bin="$PF_ALIASES_DIR/slides/node_modules/.bin/node"
    if [[ ! -x "$cli" || ! -x "$node_bin" ]]; then
        echo "Install export dependencies first: (cd \"$PF_ALIASES_DIR/slides\" && PLAYWRIGHT_BROWSERS_PATH=\"\$PWD/.playwright\" npm ci)" >&2
        return 1
    fi

    # Prefer an available project-local browser, including a reused installation.
    for browser in "$PF_ALIASES_DIR"/slides/.playwright/chromium-*/chrome-linux64/chrome; do
        if [[ -x "$browser" ]]; then
            browser_args=(--executable-path "$browser")
            break
        fi
    done

    PLAYWRIGHT_BROWSERS_PATH="$PF_ALIASES_DIR/slides/.playwright" \
        "$node_bin" "$cli" export "$input" --format pptx --output "$output" \
        --wait-until domcontentloaded --wait 1000 "${browser_args[@]}" "$@"
}
