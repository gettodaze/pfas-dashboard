export function PubChemLink({ cid }: { cid: string }) {
  return (
    <a
      href={`https://pubchem.ncbi.nlm.nih.gov/compound/${encodeURIComponent(cid)}#section=3D-Status`}
      target="_blank"
      rel="noopener noreferrer"
      title={`Open representative CID ${cid} on PubChem`}
    >
      {cid}
    </a>
  );
}
