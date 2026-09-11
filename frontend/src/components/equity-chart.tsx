"use client";

import { useEffect, useRef } from "react";
import { createChart, ColorType, LineSeries } from "lightweight-charts";

type Props = {
  dates: string[];
  series: { name: string; values: number[]; color: string }[];
  height?: number;
};

export default function EquityChart({ dates, series, height = 280 }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || dates.length === 0) return;
    const chart = createChart(ref.current, {
      height,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#71717a" },
      grid: { vertLines: { color: "#f4f4f5" }, horzLines: { color: "#f4f4f5" } },
      rightPriceScale: { borderColor: "#e4e4e7" },
      timeScale: { borderColor: "#e4e4e7" },
      autoSize: true,
    });
    // Collapse multiple snapshots on the same calendar day down to the last one —
    // lightweight-charts requires strictly ascending, unique time values, and
    // truncating ISO timestamps to a date can otherwise produce duplicates.
    const lastIndexByDay = new Map<string, number>();
    dates.forEach((d, i) => lastIndexByDay.set(d.slice(0, 10), i));
    const days = Array.from(lastIndexByDay.keys()).sort();

    for (const s of series) {
      const line = chart.addSeries(LineSeries, { color: s.color, lineWidth: 2, title: s.name });
      line.setData(
        days
          .map((day) => ({ time: day, value: s.values[lastIndexByDay.get(day)!] }))
          .filter((p) => p.value != null) as { time: string; value: number }[]
      );
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [dates, series, height]);

  if (dates.length === 0)
    return <div className="text-sm text-zinc-400 py-10 text-center">No data yet</div>;
  return <div ref={ref} className="w-full" />;
}
