"""External input discovery, conservative namelist edits, and QE RAM reports."""

import hashlib
import re
from decimal import Decimal


def discover(root, candidates):
    root = root.resolve()
    result = {}
    if not root.is_dir():
        return result
    for owner in candidates:
        directory = root / owner
        if not directory.resolve().is_relative_to(root) or not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.in")):
            match = re.fullmatch(r"(candidate|complex|tfa)\.([\w.-]+)\.in", path.name)
            if not match or (owner == "tfa") != (match[1] == "tfa"):
                continue
            if not path.resolve().is_relative_to(root) or not path.is_file():
                continue
            identifier = hashlib.sha256(
                str(path.relative_to(root)).encode()
            ).hexdigest()
            result[identifier] = (owner, match[1], path)
    return result


def mask(text, strings=True):
    """Blank comments/strings while retaining offsets and newlines (Fortran quotes)."""
    out = list(text)
    i = 0
    while i < len(text):
        if text[i] == "!":
            end = text.find("\n", i)
            end = len(text) if end < 0 else end
            out[i:end] = " " * (end - i)
            i = end
        elif text[i] in "'\"":
            start, quote = i, text[i]
            i += 1
            while i < len(text):
                if text[i] == quote:
                    if i + 1 < len(text) and text[i + 1] == quote:
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            else:
                raise ValueError("Unterminated input string")
            if strings:
                out[start:i] = ["\n" if c == "\n" else " " for c in text[start:i]]
        else:
            i += 1
    return "".join(out)


def control_span(text):
    clean = mask(text)
    starts = list(re.finditer(r"&control\b", clean, re.IGNORECASE))
    if len(starts) != 1:
        raise ValueError("Input requires exactly one &CONTROL block")
    start = starts[0].end()
    end = clean.find("/", start)
    if end < 0 or "&" in clean[start:end]:
        raise ValueError("Malformed &CONTROL block")
    return start, end, clean


def dry_input(text):
    start, end, clean = control_span(text)
    assignments = list(re.finditer(r"\bnstep\b", clean[start:end], re.IGNORECASE))
    edits = []
    for name in assignments:
        offset = start + name.start()
        value = re.match(
            r"nstep\s*=\s*[+-]?\d+(?=\s*(?:,|$|[a-z_]\w*\s*=))",
            clean[offset:end],
            re.IGNORECASE,
        )
        if not value:
            raise ValueError("Malformed CONTROL nstep assignment")
        edits.append((offset, offset + value.end(), "nstep=0"))
    if not edits:
        edits = [(start, start, "\n nstep=0,\n")]
    for a, b, replacement in reversed(edits):
        text = text[:a] + replacement + text[b:]
    return text


def validate_references(text):
    start, end, _ = control_span(text)
    body = text[start:end]
    clean = mask(body)
    requirements = {"pseudo_dir": "./Pseudopotentials", "outdir": "./Outputs"}
    for key in (*requirements, "wfcdir", "prefix"):
        names = list(re.finditer(rf"\b{key}\b\s*=", clean, re.IGNORECASE))
        if len(names) > 1 or (key in requirements and len(names) != 1):
            raise ValueError(f"Input requires one {key} assignment")
        if not names:
            continue
        value = re.match(
            r"\s*(['\"])(.*?)\1(?=\s|,|$)",
            mask(body, strings=False)[names[0].end() :],
            re.DOTALL,
        )
        if not value:
            raise ValueError(f"Invalid {key} reference")
        reference = value[2]
        expected = requirements.get(key, "./Outputs" if key == "wfcdir" else None)
        if expected is not None and reference != expected:
            raise ValueError(f"Input must use {key}='{expected}'")
        if key == "prefix" and (
            "/" in reference or "\\" in reference or reference in ("", ".", "..")
        ):
            raise ValueError("Unsafe scratch reference")


def ram_reports(lines):
    result = {"per_process": None, "total": None}
    error = False
    pattern = re.compile(
        r"Estimated\s+(max\s+dynamical\s+RAM\s+per\s+process|total\s+dynamical\s+RAM)\s*>?\s*[:=]?\s*(\d+(?:\.\d*)?(?:[EeDd][+-]?\d+)?)\s*(MB|GB)\b",
        re.IGNORECASE,
    )
    for line in lines:
        error |= bool(
            re.search(r"Error in routine|%{5,}|MPI_ABORT", line, re.IGNORECASE)
        )
        match = pattern.search(line)
        if match:
            value, unit = match[2], match[3]
            amount = Decimal(value.replace("D", "E").replace("d", "e"))
            if amount.is_finite() and amount >= 0:
                result[
                    "per_process" if match[1].lower().startswith("max") else "total"
                ] = {
                    "value": value,
                    "unit": unit,
                    "bytes": int(amount * (1024 ** (2 if unit.upper() == "MB" else 3))),
                }
    return result, error
