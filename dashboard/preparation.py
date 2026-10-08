"""Preparation-only child entry point. Never imports an execution wrapper."""

import hashlib
import json
import shutil
import sys
from pathlib import Path


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def pseudo_names(text):
    import re

    # Restrict references to the ATOMIC_SPECIES card, including malformed rows.
    lines = [line.split("!")[0].strip() for line in text.splitlines()]
    starts = [i for i, line in enumerate(lines) if line.upper() == "ATOMIC_SPECIES"]
    if len(starts) != 1:
        raise ValueError("Missing or duplicate ATOMIC_SPECIES")
    names = []
    for line in lines[starts[0] + 1 :]:
        if not line:
            continue
        values = line.split()
        if values[0].upper() in (
            "ATOMIC_POSITIONS",
            "K_POINTS",
            "CELL_PARAMETERS",
            "ATOMIC_FORCES",
            "CONSTRAINTS",
            "OCCUPATIONS",
            "HUBBARD",
            "SOLVENTS",
            "ADDITIONAL_K_POINTS",
        ):
            break
        if (
            len(values) != 3
            or not re.fullmatch(r"[A-Za-z]\w*", values[0])
            or not re.fullmatch(
                r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eEdD][+-]?\d+)?", values[1]
            )
        ):
            raise ValueError("Invalid ATOMIC_SPECIES row")
        name = values[2]
        if Path(name).name != name or "\\" in name or not name.lower().endswith(".upf"):
            raise ValueError("Missing or unsafe ATOMIC_SPECIES pseudopotentials")
        names.append(name)
    if not names:
        raise ValueError("Missing or unsafe ATOMIC_SPECIES pseudopotentials")
    count = re.search(r"\bntyp\s*=\s*(\d+)", text, re.IGNORECASE)
    if count and len(names) != int(count[1]):
        raise ValueError("ATOMIC_SPECIES count does not match ntyp")
    return sorted(set(names))


def prepare(request, directory):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "qespresso_pipeline"))
    from run_adsorption_case import (
        build_molecular_complex_cif,
        get_mode_settings,
        patch_qe_input,
        prepare_from_smiles,
    )
    from smiles_to_qe import run_cif2cell

    directory = Path(directory)
    settings = get_mode_settings("cluster", "molecule")
    if request.get("shared_tfa"):
        shared = Path(request["shared_tfa"])
        for suffix in ("mol", "cif", "in"):
            shutil.copyfile(shared / ("tfa." + suffix), directory / ("tfa." + suffix))
        tfa_mol = directory / "tfa.mol"
    else:
        tfa_mol, _, _ = prepare_from_smiles(
            "O=C(O)C(F)(F)F", directory / "tfa", settings
        )
    if request["candidate"] != "tfa":
        mol, _, _ = prepare_from_smiles(
            request["smiles"], directory / "candidate", settings
        )
        build_molecular_complex_cif(
            mol, tfa_mol, directory / "complex.cif", padding=12.0, vdw_gap=2.5
        )
        patch_qe_input(
            run_cif2cell(directory / "complex.cif", str(directory / "complex")),
            settings,
            1,
            0.0,
        )
    inputs = list(directory.glob("*.in"))
    hashes = {}
    for path in inputs:
        for name in pseudo_names(path.read_text()):
            hashes[name] = digest(Path(request["pseudos"]) / name)
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "settings": settings,
                "geometry": {"padding": 12, "vdw_gap": 2.5},
                "source": request,
                "inputs": {p.name: digest(p) for p in inputs},
                "pseudopotentials": hashes,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    prepare(json.loads(Path(sys.argv[1]).read_text()), sys.argv[2])
