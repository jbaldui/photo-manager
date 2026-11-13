import { useState } from "react";

/** Drag the handle to compare the original (left) with the enhanced image (right). Same framing. */
export default function BeforeAfter({ before, after, label = ["Original", "Enhanced"] }: { before: string; after: string; label?: [string, string] }) {
  const [pos, setPos] = useState(50);
  return (
    <div className="ba">
      <img src={after} alt={label[1]} className="ba-after" />
      <img src={before} alt={label[0]} className="ba-before" style={{ clipPath: `inset(0 ${100 - pos}% 0 0)` }} />
      <div className="ba-line" style={{ left: `${pos}%` }} />
      <span className="ba-tag" style={{ left: 8 }}>{label[0]}</span>
      <span className="ba-tag" style={{ right: 8 }}>{label[1]}</span>
      <input
        type="range"
        min={0}
        max={100}
        step={0.5}
        value={pos}
        onChange={(e) => setPos(Number(e.target.value))}
        aria-label="Compare original and enhanced"
      />
    </div>
  );
}
