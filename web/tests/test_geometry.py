import pytest

from dashboard.geometry import BOHR, geometry


def test_fractional_skew_cell_and_alat():
    result = geometry("""&SYSTEM
ibrav=0, A=10, nat=2
/
CELL_PARAMETERS {alat}
1 0 0
0.2 1 0
0 0 2
ATOMIC_POSITIONS {crystal}
C .5 .5 .5
H .1 .2 .3 1 1 1
""")
    assert result["atoms"][0]["position"] == pytest.approx([6, 5, 10])
    assert result["atoms"][1]["position"] == pytest.approx([1.4, 2, 6])
    assert len(result["input_hash"]) == 64


@pytest.mark.parametrize(
    "unit,expected", [("angstrom", 1), ("bohr", BOHR), ("alat", 2 * BOHR)]
)
def test_cartesian_units(unit, expected):
    result = geometry(f"""&SYSTEM
nat=1, celldm(1)=2d0
/
ATOMIC_POSITIONS ({unit})
H 1D0 0 0
""")
    assert result["atoms"][0]["position"][0] == pytest.approx(expected)


def test_unsupported_cell_and_incomplete_coordinates():
    with pytest.raises(ValueError, match="explicit cell"):
        geometry("ibrav=1, nat=1\nATOMIC_POSITIONS crystal\nH 0 0 0")
    with pytest.raises(ValueError, match="Incomplete"):
        geometry("nat=2\nATOMIC_POSITIONS angstrom\nH 0 0 0")
