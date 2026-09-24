"use client";

import { useState } from "react";
import { usd } from "@/lib/lab";

export type Point = { x: number; y: number; id: number; highlight?: boolean };

/** Past (x) against future (y), one dot per parameter set. If the cloud rises
 * from left to right, the past ranking predicts the future one; if it is a
 * shapeless blob, choosing the best past result was choosing at random. */
export default function Scatter({
  points, color, lines = [], height = 320, xLabel, yLabel, onPick,
}: {
  points: Point[];
  color: string;
  lines?: { y: number; label: string; color: string }[];
  height?: number;
  xLabel: string;
  yLabel: string;
  onPick?: (id: number) => void;
}) {
  const [hover, setHover] = useState<Point | null>(null);
  const W = 640, H = height, L = 64, R = 12, T = 12, B = 36;
  const xs = points.map((p) => p.x), ys = [...points.map((p) => p.y), ...lines.map((l) => l.y), 0];
  const x0 = Math.min(...xs, 0), x1 = Math.max(...xs, 0), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const px = (v: number) => L + ((v - x0) / (x1 - x0 || 1)) * (W - L - R);
  const py = (v: number) => T + (1 - (v - y0) / (y1 - y0 || 1)) * (H - T - B);
  const ticks = (a: number, b: number) => Array.from({ length: 5 }, (_, i) => a + ((b - a) * i) / 4);
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={`${yLabel} en fonction de ${xLabel}`}>
        {ticks(y0, y1).map((t) => (
          <g key={`y${t}`}>
            <line x1={L} x2={W - R} y1={py(t)} y2={py(t)} stroke="#f1f1f3" />
            <text x={L - 6} y={py(t) + 3} textAnchor="end" fontSize="10" fill="#71717a">{usd(t)}</text>
          </g>
        ))}
        {ticks(x0, x1).map((t) => (
          <text key={`x${t}`} x={px(t)} y={H - B + 14} textAnchor="middle" fontSize="10" fill="#71717a">{usd(t)}</text>
        ))}
        <line x1={L} x2={W - R} y1={py(0)} y2={py(0)} stroke="#a1a1aa" />
        <line x1={px(0)} x2={px(0)} y1={T} y2={H - B} stroke="#a1a1aa" />
        {lines.map((l) => (
          <g key={l.label}>
            <line x1={L} x2={W - R} y1={py(l.y)} y2={py(l.y)} stroke={l.color} strokeDasharray="4 4" />
            <text x={W - R - 4} y={py(l.y) - 4} textAnchor="end" fontSize="10" fill={l.color}>{l.label}</text>
          </g>
        ))}
        {points.map((p) => (
          <circle key={p.id} cx={px(p.x)} cy={py(p.y)} r={p.highlight ? 6 : 4}
            fill={p.highlight ? "#7c3aed" : color} fillOpacity={p.highlight ? 1 : 0.55}
            stroke={p.highlight ? "#fff" : "none"} strokeWidth={2} className="cursor-pointer"
            onMouseEnter={() => setHover(p)} onMouseLeave={() => setHover(null)} onClick={() => onPick?.(p.id)} />
        ))}
        <text x={(L + W - R) / 2} y={H - 4} textAnchor="middle" fontSize="11" fill="#52525b">{xLabel} →</text>
        <text x={12} y={(T + H - B) / 2} textAnchor="middle" fontSize="11" fill="#52525b"
          transform={`rotate(-90 12 ${(T + H - B) / 2})`}>{yLabel} →</text>
      </svg>
      {hover && (
        <div className="pointer-events-none absolute right-2 top-2 rounded-md bg-zinc-900 px-2 py-1 text-xs text-white">
          passé {usd(hover.x, { signed: true })} · futur {usd(hover.y, { signed: true })}
        </div>
      )}
    </div>
  );
}
