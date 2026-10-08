import { InputAsset } from "./data";

export function InputDependencies({ asset }: { asset?: InputAsset }) {
  if (!asset) return null;
  return (
    <details>
      <summary>Pseudopotentials and input hash</summary>
      <p>
        Input SHA-256: <code>{asset.sha256}</code>
      </p>
      {asset.dependency_error && <p>{asset.dependency_error}</p>}
      {asset.pseudopotentials.map((pseudo) => (
        <p key={pseudo.name}>
          {pseudo.url ? (
            <a href={pseudo.url} download={pseudo.name}>
              {pseudo.name}
            </a>
          ) : (
            <span>{pseudo.name} · File unavailable</span>
          )}
          {pseudo.sha256 && (
            <>
              {" "}
              · SHA-256: <code>{pseudo.sha256}</code>
            </>
          )}
        </p>
      ))}
    </details>
  );
}
