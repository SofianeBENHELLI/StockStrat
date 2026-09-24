"use client";

import { useEffect, useRef } from "react";
import { ColorType, createChart, LineSeries, LineStyle } from "lightweight-charts";

export type ChartSeries = { name: string; values: number[]; color: string; dashed?: boolean; width?: 1 | 2 | 3 };

/** Equity in dollars over time, several series on one axis. The main series is
 * drawn solid; comparisons (SPY, placebo, backtest replay) dashed and thinner,
 * so the eye lands on the model first. */
export default function EquityChart({
  dates,
  series,
  height = 300,
}: {
  dates: string[];
  series: ChartSeries[];
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || dates.length === 0) return;
    const chart = createChart(ref.current, {
      height,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#71717a", fontSize: 11 },
      grid: { vertLines: { visible: false }, horzLines: { color: "#f1f1f3" } },
      rightPriceScale: { borderVisible: false },
      // The default minimum spacing (0.5 px per bar) cannot fit ten years of
      // daily points in a normal-width card, and the library then silently
      // drops the start of the series instead of compressing it.
      timeScale: { borderVisible: false, minBarSpacing: 0.02 },
      // Charts sit inside long, scrolling pages: the wheel must scroll the page,
      // not zoom the chart. Drag and pinch still explore it.
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true },
      crosshair: { horzLine: { labelBackgroundColor: "#18181b" }, vertLine: { labelBackgroundColor: "#18181b" } },
      localization: {
        priceFormatter: (p: number) => `${Math.round(p).toLocaleString("fr-FR")} $`,
      },
      autoSize: true,
    });
    const lastIndexByDay = new Map<string, number>();
    dates.forEach((d, i) => lastIndexByDay.set(d.slice(0, 10), i));
    const days = Array.from(lastIndexByDay.keys()).sort();
    for (const s of series) {
      const line = chart.addSeries(LineSeries, {
        color: s.color,
        lineWidth: s.width ?? (s.dashed ? 1 : 2),
        lineStyle: s.dashed ? LineStyle.Dashed : LineStyle.Solid,
        title: s.name,
        priceLineVisible: false,
        lastValueVisible: !s.dashed,
      });
      line.setData(
        days
          .map((day) => ({ time: day, value: s.values[lastIndexByDay.get(day)!] }))
          .filter((p) => p.value != null && !Number.isNaN(p.value)) as { time: string; value: number }[]
      );
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [dates, series, height]);

  if (dates.length === 0)
    return <div className="text-sm text-muted-foreground py-10 text-center">Pas encore de données</div>;
  return <div ref={ref} className="w-full" />;
}
