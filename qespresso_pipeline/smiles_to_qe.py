#!/usr/bin/env python3

import argparse
import subprocess
import sys
import shutil
import re
from typing import Optional
import numpy as np
from pymatgen.core import Molecule, Lattice, Structure
from pymatgen.io.cif import CifWriter
from pathlib import Path


def check_executable(name: str):
    if shutil.which(name) is None and not Path(name).exists():
        print(f"Error: '{name}' not found.", file=sys.stderr)
        sys.exit(1)


# Generation ladder for 3-D structures (audit A7).
# Rung 1 is the historical configuration (MMFF94 + conformer search). MMFF94 has
# no parameters for dissociated ionic pairs and obabel's conformer search can
# segfault on them (observed: CTAB "CCCCCCCCCCCCCCCC[N+](C)(C)C.[Br-]",
# molecular_adsorbents_smiles.csv row 001, 2026-09-12). Lower rungs trade
# geometry quality for robustness and are only reached when the rung above
# fails, so behavior for neutral species is unchanged. Note: obabel's --ff,
# --conformer, and --minimize flags belong to the same (conformer search)
# interface, so a "force field without conformer search" combination is not
# expressible — the final rung is plain --gen3d distance geometry.
OBABEL_LADDER = [
    ("MMFF94", True),  # status quo: best geometry for neutral species
    ("UFF", True),  # UFF parameterizes ionic species
    (None, False),  # plain --gen3d: distance geometry only, last resort
]


def _obabel_command(
    input_string: str, output_mol: Path, force_field: Optional[str], conformer: bool
) -> list:
    command = ["obabel", f"-:{input_string}", "-O", str(output_mol), "--gen3d"]
    if conformer:
        command += ["--conformer", "--minimize", "--steps", "1000"]
        if force_field:
            command += ["--ff", force_field]
    return command


def run_obabel(
    input_string: str,
    output_name: str,
    force_field: Optional[str] = None,
    conformer: Optional[bool] = None,
) -> Path:
    """Generate a 3-D MOL file from a SMILES string.

    With defaults, the OBABEL_LADDER rungs are tried in order and the first
    rung that produces a non-empty MOL file wins. Passing force_field and/or
    conformer pins exactly one configuration (no fallback) — used by the
    --force-field / --no-conformer CLI flags. --no-conformer implies plain
    --gen3d (the force field applies only to the conformer search).
    """
    output_mol = Path(f"{output_name}.mol")

    if force_field is not None or conformer is not None:
        ladder = [(force_field, True if conformer is None else conformer)]
    else:
        ladder = OBABEL_LADDER

    def _label(ff: Optional[str], conf: bool) -> str:
        return f"{ff or 'no-ff'} + {'conformer' if conf else 'gen3d-only'}"

    print(f"[info] Creating MOL: {output_mol}")
    last_error = None
    for ff, use_conformer in ladder:
        if output_mol.exists():
            output_mol.unlink()  # stale output from a failed rung must not mask the next attempt

        command = _obabel_command(input_string, output_mol, ff, use_conformer)
        print(f"[info] obabel rung: {_label(ff, use_conformer)}")

        try:
            # We must catch both standard exit codes and actual system crashes
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as e:
            if e.returncode is not None and e.returncode < 0:
                detail = f"killed by signal {-e.returncode} (crash/segfault)"
                print(
                    f"[warn] Open Babel crashed (SIGSEGV) on {_label(ff, use_conformer)}. Forcing basic gen3d fallback.",
                    file=sys.stderr,
                )
            else:
                detail = f"exit code {e.returncode}"

            print(
                f"[warn] obabel failed ({_label(ff, use_conformer)}): {detail}; trying next rung",
                file=sys.stderr,
            )
            last_error = e
            continue

        if output_mol.exists() and output_mol.stat().st_size > 0:
            print(
                f"[info] MOL created: {output_mol} (rung: {_label(ff, use_conformer)})"
            )
            return output_mol

        print(
            f"[warn] obabel rung {_label(ff, use_conformer)} produced no MOL output; trying next rung",
            file=sys.stderr,
        )

    tried = ", ".join(_label(ff, conf) for ff, conf in ladder)
    if last_error is not None:
        print(
            f"[error] last obabel failure: returncode {last_error.returncode}",
            file=sys.stderr,
        )
    raise RuntimeError(
        f"obabel could not generate a 3-D structure for '{input_string}' (tried: {tried}). "
        "If this is an ionic/dissociated SMILES, try --force-field UFF or --no-conformer, "
        "or provide a structure file directly (--adsorbent-source cif)."
    )


def mol_to_cif_pymatgen(
    input_mol: Path, output_name: str, padding: float = 12.0
) -> Path:
    output_cif = Path(f"{output_name}.cif")
    # Register MOL readers before filename-based discovery (pymatgen-core 2026.8.30).
    import pymatgen.io.babel  # noqa: F401

    print(f"[info] Creating CIF from MOL: {output_cif}")

    mol = Molecule.from_file(str(input_mol))
    coords = np.array(mol.cart_coords)

    mins = coords.min(axis=0)
    maxs = coords.max(axis=0)
    lengths = maxs - mins

    box_lengths = lengths + padding
    lattice = Lattice.orthorhombic(*box_lengths)
    shifted_coords = coords - mins + padding / 2

    structure = Structure(
        lattice,
        mol.species,
        shifted_coords,
        coords_are_cartesian=True,
    )

    CifWriter(structure, symprec=None).write_file(str(output_cif))

    if not output_cif.exists():
        raise FileNotFoundError(f"CIF file not created: {output_cif}")

    print(f"[info] CIF created: {output_cif} (box = {box_lengths})")
    return output_cif


def run_cif2cell(input_cif: Path, output_name: str) -> Path:
    output_in = Path(f"{output_name}.in")
    command = [
        "cif2cell",
        str(input_cif),
        "-p",
        "quantum-espresso",
        "-o",
        str(output_in),
    ]
    print(f"[info] Creating QE input: {output_in}")
    subprocess.run(command, check=True)

    if not output_in.exists():
        raise FileNotFoundError(f"QE input not created: {output_in}")
    return output_in


def modify_qe_input(
    input_file,
    output_file=None,
    job_type="molecule",
    ecutwfc=60,
    ecutrho=600,
    use_gamma=True,
    mixing_beta=None,
    input_dft="pbe",
    kpts=(1, 1, 1),
    calculation="relax",
    nspin=1,
    tot_magnetization=0.0,
):
    input_path = Path(input_file)
    prefix = input_path.stem

    if output_file is None:
        output_file = input_path

    with open(input_path, "r") as f:
        lines = f.readlines()

    header = []
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("&SYSTEM"):
            body_start = i
            break
        header.append(line)

    body = lines[body_start:]

    system_extra = [
        f"  ecutwfc={ecutwfc},\n",
        f"  ecutrho={ecutrho},\n",
        f"  input_dft='{input_dft}',\n",
    ]

    if "vdW" not in input_dft and "vdw" not in input_dft:
        system_extra.append("  vdw_corr='dft-d3',\n")

    if nspin == 2:
        system_extra.extend(
            [f"  nspin=2,\n", f"  tot_magnetization={tot_magnetization},\n"]
        )

    if job_type == "molecule":
        if mixing_beta is None:
            mixing_beta = 0.2
        system_extra.extend(
            [
                "  assume_isolated='mt',\n",
                "  occupations='smearing',\n",
                "  smearing='m-p',\n",
                "  degauss=0.005d0,\n",
            ]
        )
        electrons_block = f"&ELECTRONS\n  conv_thr=1d-07,\n  mixing_beta={mixing_beta}d0,\n  electron_maxstep=200,\n/\n"
        kpoints_block = (
            "K_POINTS gamma\n"
            if use_gamma
            else f"K_POINTS {{automatic}}\n  {kpts[0]} {kpts[1]} {kpts[2]} 0 0 0\n"
        )
    else:
        if mixing_beta is None:
            mixing_beta = 0.4
        system_extra.extend(
            [
                "  occupations='smearing',\n",
                "  smearing='cold',\n",
                "  degauss=0.01d0,\n",
            ]
        )
        electrons_block = f"&ELECTRONS\n  conv_thr=1d-06,\n  mixing_beta={mixing_beta}d0,\n  electron_maxstep=150,\n/\n"
        kpoints_block = (
            f"K_POINTS {{automatic}}\n  {kpts[0]} {kpts[1]} {kpts[2]} 0 0 0\n"
        )

    ions_block = "&IONS\n  ion_dynamics='bfgs',\n/\n"

    system_block = []
    i = 0
    while i < len(body):
        line = body[i]
        if line.strip().startswith("&SYSTEM"):
            system_block.append("&SYSTEM\n")
            i += 1
            while i < len(body) and not body[i].strip().startswith("/"):
                system_block.append(body[i])
                i += 1
            system_block.extend(system_extra)
            system_block.append("/\n")
            i += 1
        else:
            system_block.append(line)
            i += 1

    fixed_body = []
    for line in system_block:
        line = re.sub(r"\b([A-Za-z]+)_PSEUDO\b", r"\1.UPF", line)
        fixed_body.append(line)

    control_block = f"""&CONTROL
  calculation='{calculation}',
  outdir='./Outputs',
  prefix='{prefix}',
  pseudo_dir='./Pseudopotentials',
  verbosity='low',
  tprnfor=.true.,
  tstress=.true.,
  forc_conv_thr=1.0d-4,
  disk_io='low',
/
"""

    final_body = []
    inserted_electrons = False
    for line in fixed_body:
        final_body.append(line)
        if line.strip() == "/" and not inserted_electrons:
            final_body.append("\n")
            final_body.append(electrons_block)
            if calculation == "relax" or calculation == "vc-relax":
                final_body.append(ions_block)
            inserted_electrons = True

    with open(output_file, "w") as f:
        f.writelines(header)
        f.write(control_block)
        f.writelines(final_body)
        f.write("\n")
        f.write(kpoints_block)

    print(f"[info] Patched QE input: {output_file}")


def main():
    parser = argparse.ArgumentParser(description="SMILES -> MOL -> CIF -> QE input")
    parser.add_argument("input", help="Input SMILES")
    parser.add_argument("output", help="Base output filename")
    parser.add_argument("--padding", type=float, default=10.0)
    parser.add_argument(
        "--job-type", choices=["molecule", "periodic"], default="molecule"
    )
    parser.add_argument("--ecutwfc", type=float, default=35)
    parser.add_argument("--ecutrho", type=float, default=280)
    parser.add_argument("--input-dft", default="pbe")
    parser.add_argument("--kpts", nargs=3, type=int, default=[1, 1, 1])
    parser.add_argument("--no-gamma", action="store_true")
    parser.add_argument(
        "--force-field",
        choices=["MMFF94", "UFF", "GAFF"],
        default=None,
        help="pin one obabel force field (default: automatic MMFF94->UFF->gen3d fallback)",
    )
    parser.add_argument(
        "--no-conformer",
        action="store_true",
        help="skip the obabel conformer search (combined with --force-field)",
    )
    args = parser.parse_args()

    check_executable("obabel")
    check_executable("cif2cell")

    try:
        mol_file = run_obabel(
            args.input,
            args.output,
            force_field=args.force_field,
            conformer=False if args.no_conformer else None,
        )
        cif_file = mol_to_cif_pymatgen(mol_file, args.output, padding=args.padding)
        qe_input = run_cif2cell(cif_file, args.output)
        modify_qe_input(
            qe_input,
            job_type=args.job_type,
            ecutwfc=args.ecutwfc,
            ecutrho=args.ecutrho,
            use_gamma=not args.no_gamma,
            input_dft=args.input_dft,
            kpts=tuple(args.kpts),
        )

        print("\n[done] Pipeline completed successfully.")
        print(f"  - {mol_file}")
        print(f"  - {cif_file}")
        print(f"  - {qe_input}")

    except subprocess.CalledProcessError as e:
        print(f"External command failed with exit code {e.returncode}", file=sys.stderr)
        sys.exit(e.returncode)
    except RuntimeError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
