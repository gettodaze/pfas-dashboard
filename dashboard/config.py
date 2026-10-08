import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def preparation_python():
    configured = os.getenv("PFAS_CHEM_PYTHON")
    if configured:
        return configured
    return sys.executable


@dataclass
class Config:
    artifacts: Path = field(
        default_factory=lambda: Path(
            os.getenv("PFAS_ARTIFACTS", ROOT / ".dashboard")
        ).resolve()
    )
    apptainer: str = field(
        default_factory=lambda: os.getenv("PFAS_APPTAINER", "apptainer")
    )
    image: Path | None = field(
        default_factory=lambda: (
            Path(os.environ["PFAS_APPTAINER_IMAGE"]).resolve()
            if os.getenv("PFAS_APPTAINER_IMAGE")
            else None
        )
    )
    python: str = field(
        default_factory=lambda: os.getenv("PFAS_CHEM_PYTHON", sys.executable)
    )
    prepare_python: str = field(default_factory=preparation_python)
    pseudos: Path = field(
        default_factory=lambda: Path(
            os.getenv("PFAS_PSEUDOS", ROOT / "qespresso_pipeline/Pseudopotentials")
        ).resolve()
    )
    qe_inputs: Path = field(
        default_factory=lambda: Path(
            os.getenv("PFAS_QE_INPUTS", ROOT / "qe_inputs")
        ).resolve()
    )
    pw: str = field(default_factory=lambda: os.getenv("PFAS_PW", "pw.x"))
    mpi: str = field(default_factory=lambda: os.getenv("PFAS_MPI", "mpirun"))
    prepare_timeout: float = field(
        default_factory=lambda: float(os.getenv("PFAS_PREPARE_TIMEOUT", "900"))
    )
