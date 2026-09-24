/** A tiny equity line. The baseline is the starting value, so above/below the
 * dotted line reads as "making / losing money" at a glance. */
export default function Sparkline({
  values,
  width = 120,
  height = 32,
  color,
  baseline,
}: {
  values: number[];
  width?: number;
  height?: number;
  color?: string;
  baseline?: number;
}) {
  if (!values || values.length < 2) {
    return <svg width={width} height={height} aria-hidden className="text-muted-foreground/30">
      <line x1={0} x2={width} y1={height / 2} y2={height / 2} stroke="currentColor" strokeDasharray="2 3" />
    </svg>;
  }
  const base = baseline ?? values[0];
  const lo = Math.min(...values, base);
  const hi = Math.max(...values, base);
  const span = hi - lo || 1;
  const x = (i: number) => (i / (values.length - 1)) * width;
  const y = (v: number) => height - 2 - ((v - lo) / span) * (height - 4);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const last = values[values.length - 1];
  const stroke = color ?? (last >= base ? "#059669" : "#dc2626");
  return (
    <svg width={width} height={height} aria-hidden>
      <line x1={0} x2={width} y1={y(base)} y2={y(base)} stroke="#a1a1aa" strokeDasharray="2 3" strokeWidth={1} />
      <path d={d} fill="none" stroke={stroke} strokeWidth={1.6} strokeLinejoin="round" />
    </svg>
  );
}
