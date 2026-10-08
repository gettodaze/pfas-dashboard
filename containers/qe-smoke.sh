#!/bin/sh
# Run QE initialization in a writable directory, even for a read-only SIF.
set -eu
smoke_dir=$(mktemp -d /tmp/pfas-qe-smoke.XXXXXX)
trap 'rm -rf "$smoke_dir"' EXIT
trap 'exit 1' HUP INT TERM
cd "$smoke_dir"
cp /opt/pfas-image/H.UPF H.UPF
cat > input.in <<'EOF'
&CONTROL
 calculation='scf', prefix='h2', pseudo_dir='.', outdir='./outputs', nstep=0
/
&SYSTEM
 ibrav=1, celldm(1)=12, nat=2, ntyp=1, ecutwfc=15, ecutrho=120
/
&ELECTRONS
 conv_thr=1d-6
/
ATOMIC_SPECIES
H 1.00794 H.UPF
ATOMIC_POSITIONS angstrom
H 3 3 3
H 3 3 3.74
K_POINTS gamma
EOF
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
# Fakeroot builds run as root; oversubscribe also permits testing on one CPU.
smoke_status=0
timeout 120 mpirun --allow-run-as-root --oversubscribe -np 2 \
    pw.x -in input.in > output.log 2>&1 || smoke_status=$?
cat output.log
# Builds enabling __RETURN_EXIT_STATUS return 255 for nstep=0; others return 0.
case "$smoke_status" in
    0|255) ;;
    *) exit 1 ;;
esac
grep -Eq 'Program PWSCF v\.7\.6([[:space:]]|$)' output.log
grep -Eq 'Parallel version \(MPI\), running on[[:space:]]+2 processors' output.log
grep -q 'JOB DONE\.' output.log
