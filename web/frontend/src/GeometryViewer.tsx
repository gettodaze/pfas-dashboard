import { useEffect, useRef, useState } from "react";
import { Geometry } from "./data";

const colors: Record<string, string> = {
  H: "#d9e0e5",
  C: "#555d66",
  O: "#e34d4d",
  F: "#62bb67",
  N: "#568be0",
  S: "#d9b532",
  Cl: "#45a254",
};
const radii: Record<string, number> = {
  H: 0.31,
  C: 0.76,
  O: 0.66,
  F: 0.57,
  N: 0.71,
  S: 1.05,
  Cl: 1.02,
};

export function GeometryViewer({
  geometries,
}: {
  geometries: Record<string, Geometry>;
}) {
  const systems = Object.keys(geometries);
  const [selected, setSelected] = useState(
    systems.includes("complex") ? "complex" : systems[0],
  );
  const [rotation, setRotation] = useState([0.25, -0.3]);
  const [zoom, setZoom] = useState(1.25);
  const canvas = useRef<HTMLCanvasElement>(null);
  const drag = useRef<number[] | null>(null);
  const geometry = geometries[selected] || geometries[systems[0]];
  const atoms = geometry?.atoms;
  useEffect(() => {
    const el = canvas.current;
    if (!el || !atoms?.length) return;
    const draw = () => {
      const width = el.clientWidth,
        height = el.clientHeight;
      el.width = width * devicePixelRatio;
      el.height = height * devicePixelRatio;
      const ctx = el.getContext("2d");
      if (!ctx) return;
      ctx.scale(devicePixelRatio, devicePixelRatio);
      ctx.fillStyle = "#f5f8fa";
      ctx.fillRect(0, 0, width, height);
      const min = [0, 1, 2].map((i) =>
        Math.min(...atoms.map((a) => a.position[i])),
      );
      const max = [0, 1, 2].map((i) =>
        Math.max(...atoms.map((a) => a.position[i])),
      );
      const center = min.map((v, i) => (v + max[i]) / 2);
      const span = Math.max(3, Math.hypot(...max.map((v, i) => v - min[i])));
      const scale = ((Math.min(width, height) * 0.75) / span) * zoom;
      const [yaw, pitch] = rotation;
      const points = atoms.map((atom) => {
        const [x, y, z] = atom.position.map((v, i) => v - center[i]);
        const rx = x * Math.cos(yaw) + z * Math.sin(yaw);
        const rz = z * Math.cos(yaw) - x * Math.sin(yaw);
        return {
          atom,
          x: width / 2 + rx * scale,
          y: height / 2 - (y * Math.cos(pitch) - rz * Math.sin(pitch)) * scale,
          z: y * Math.sin(pitch) + rz * Math.cos(pitch),
        };
      });
      // Infer visual bonds from covalent radii; no periodic replicas or chemical assignment.
      if (atoms.length <= 2000) {
        ctx.strokeStyle = "#a2adb6";
        ctx.lineWidth = 2;
        for (let i = 0; i < points.length; i++)
          for (let j = i + 1; j < points.length; j++) {
            const a = points[i],
              b = points[j];
            const distance = Math.hypot(
              ...a.atom.position.map((v, k) => v - b.atom.position[k]),
            );
            const cutoff =
              1.2 *
              ((radii[a.atom.element] || 0.8) + (radii[b.atom.element] || 0.8));
            if (distance > 0.1 && distance < cutoff) {
              ctx.beginPath();
              ctx.moveTo(a.x, a.y);
              ctx.lineTo(b.x, b.y);
              ctx.stroke();
            }
          }
      }
      points
        .sort((a, b) => a.z - b.z)
        .forEach((p) => {
          ctx.beginPath();
          ctx.arc(
            p.x,
            p.y,
            Math.max(
              3,
              Math.min(22, scale * (p.atom.element === "H" ? 0.16 : 0.28)),
            ),
            0,
            Math.PI * 2,
          );
          ctx.fillStyle = colors[p.atom.element] || "#b48bc5";
          ctx.fill();
          ctx.strokeStyle = "#485463";
          ctx.lineWidth = 1;
          ctx.stroke();
        });
    };
    const observer = new ResizeObserver(draw);
    observer.observe(el);
    draw();
    return () => observer.disconnect();
  }, [atoms, rotation, zoom]);
  if (!systems.length) return null;
  function download() {
    if (!atoms) return;
    const xyz =
      `${atoms.length}\nInitial ${selected} geometry; angstrom; input SHA256 ${geometry.input_hash}\n` +
      atoms
        .map(
          (a) =>
            `${a.element} ${a.position.map((v) => v.toFixed(8)).join(" ")}`,
        )
        .join("\n") +
      "\n";
    const url = URL.createObjectURL(new Blob([xyz], { type: "text/plain" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${selected}-initial.xyz`;
    link.click();
    URL.revokeObjectURL(url);
  }
  return (
    <section className="geometry-viewer">
      <h3>Initial geometry</h3>
      <p>
        Starting coordinates from the selected input, in Å. Drag to rotate; use
        the zoom controls. Bonds are inferred for display.
      </p>
      <div className="controls">
        <label>
          System{" "}
          <select
            aria-label="Geometry system"
            value={selected}
            onChange={(e) => {
              setSelected(e.target.value);
              setZoom(1.25);
            }}
          >
            {systems.map((s) => (
              <option key={s} value={s}>
                {s === "complex"
                  ? "Candidate–TFA complex"
                  : s === "tfa"
                    ? "Neutral TFA"
                    : "Isolated candidate"}
              </option>
            ))}
          </select>
        </label>
        <button onClick={() => setZoom((z) => Math.min(8, z * 1.25))}>
          Zoom in
        </button>
        <button onClick={() => setZoom((z) => Math.max(0.2, z / 1.25))}>
          Zoom out
        </button>
        <button
          onClick={() => {
            setRotation([0.25, -0.3]);
            setZoom(1.25);
          }}
        >
          Reset view
        </button>
        <button disabled={!atoms} onClick={download}>
          Download XYZ
        </button>
      </div>
      {geometry?.error ? (
        <p className="error">Cannot display geometry: {geometry.error}</p>
      ) : (
        <>
          <canvas
            ref={canvas}
            className="geometry-canvas"
            aria-label={`Interactive initial ${selected} geometry`}
            onPointerDown={(e) => {
              drag.current = [e.clientX, e.clientY];
              e.currentTarget.setPointerCapture(e.pointerId);
            }}
            onPointerMove={(e) => {
              if (!drag.current) return;
              const [x, y] = drag.current;
              setRotation((r) => [
                r[0] + (e.clientX - x) * 0.01,
                r[1] + (e.clientY - y) * 0.01,
              ]);
              drag.current = [e.clientX, e.clientY];
            }}
            onPointerUp={() => {
              drag.current = null;
            }}
            onPointerCancel={() => {
              drag.current = null;
            }}
          />
          <p>
            {atoms?.length} atoms ·{" "}
            {Array.from(new Set(atoms?.map((a) => a.element))).map(
              (element) => (
                <span key={element} className="element-key">
                  <span style={{ background: colors[element] || "#b48bc5" }} />
                  {element}
                </span>
              ),
            )}
          </p>
        </>
      )}
    </section>
  );
}
