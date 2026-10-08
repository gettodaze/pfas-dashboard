import sys

from rdkit import Chem
from rdkit.Chem import Draw

mol = Chem.MolFromSmiles(sys.argv[1])
if mol is None:
    raise ValueError("Invalid representative SMILES")
Draw.MolToFile(mol, sys.argv[2], size=(600, 400))
