"""Read initial coordinates from QE inputs without running chemistry or QE."""

import hashlib
import math
import re

BOHR = 0.529177210903


def geometry(text):
    lines = [line.split("!")[0].strip() for line in text.splitlines()]

    def number(value):
        result = float(value.replace("D", "e").replace("d", "e"))
        if not math.isfinite(result):
            raise ValueError("Non-finite coordinate")
        return result

    def parameter(name):
        match = re.search(
            rf"\b{re.escape(name)}\s*=\s*([-+\d.eEdD]+)", text, re.IGNORECASE
        )
        return number(match[1]) if match else None

    alat = parameter("A")
    if alat is None:
        celldm = parameter("celldm(1)")
        alat = celldm * BOHR if celldm is not None else None

    def card(name):
        for i, line in enumerate(lines):
            if line.upper().startswith(name):
                unit = line[len(name) :].strip(" {}()").lower() or "alat"
                return i + 1, unit
        raise ValueError(f"Missing {name}")

    def scale(unit):
        if unit == "angstrom":
            return 1.0
        if unit == "bohr":
            return BOHR
        if unit == "alat" and alat is not None:
            return alat
        raise ValueError(f"Unsupported or unspecified length unit: {unit}")

    start, unit = card("ATOMIC_POSITIONS")
    count = parameter("nat")
    if count is None or count != int(count) or not 0 < count <= 100000:
        raise ValueError("Invalid atom count")
    cell = None
    if unit == "crystal":
        if parameter("ibrav") != 0:
            raise ValueError(
                "Fractional coordinates require an explicit cell (ibrav=0)"
            )
        cell_start, cell_unit = card("CELL_PARAMETERS")
        factor = scale(cell_unit)
        cell = [
            [number(v) * factor for v in line.split()[:3]]
            for line in lines[cell_start : cell_start + 3]
        ]
        if len(cell) != 3 or any(len(row) != 3 for row in cell):
            raise ValueError("Invalid cell")
    atoms = []
    for line in lines[start : start + int(count)]:
        values = line.split()
        if len(values) < 4:
            raise ValueError("Incomplete atomic coordinates")
        element = re.match(r"[A-Z][a-z]?", values[0])
        if not element:
            raise ValueError("Invalid atom label")
        xyz = [number(v) for v in values[1:4]]
        if cell:
            xyz = [sum(xyz[j] * cell[j][i] for j in range(3)) for i in range(3)]
        else:
            xyz = [v * scale(unit) for v in xyz]
        atoms.append({"element": element[0], "position": xyz})
    if len(atoms) != int(count):
        raise ValueError("Incomplete atomic coordinates")
    return {
        "atoms": atoms,
        "units": "angstrom",
        "input_hash": hashlib.sha256(text.encode()).hexdigest(),
    }


def prepared_geometries(task, input_path):
    if task["kind"] != "prepare" or task["status"] != "succeeded":
        return {}
    results = {}
    for system in ("candidate", "complex", "tfa"):
        path = input_path(system + ".in")
        if path and path.is_file():
            try:
                results[system] = geometry(path.read_text())
            except (ValueError, IndexError) as error:
                results[system] = {"error": str(error)}
            except OSError:
                results[system] = {"error": "Prepared input cannot be read"}
    return results
