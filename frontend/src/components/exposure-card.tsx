"use client";

import { AlertTriangle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { usd, type Exposure } from "@/lib/lab";

/** What the whole account holds, summed across models — the risk no single
 * model's page can show. */
export default function ExposureCard({ exposure }: { exposure: Exposure | null }) {
  if (!exposure || exposure.total === 0) return null;
  const top = exposure.sectors.slice(0, 7);
  const max = Math.max(...top.map((s) => s.pct), exposure.threshold_pct);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Exposition du compte, tous modèles confondus</CardTitle>
        <p className="text-xs text-muted-foreground">
          {usd(exposure.total, { signed: false })} engagés, positions et achats en attente compris. Seuil d&apos;alerte :{" "}
          {exposure.threshold_pct.toFixed(0)} % par secteur.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        {exposure.alerts.length > 0 && (
          <p className="flex gap-2 rounded-lg bg-amber-500/10 px-3 py-2 text-sm text-amber-800">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <span>
              {exposure.alerts.map((a) => `${a.sector} ${a.pct.toFixed(0)} % (${a.models} modèles)`).join(" · ")} — plusieurs
              modèles misent sur la même chose : un décrochage de ce secteur les touchera ensemble.
            </span>
          </p>
        )}
        <div className="space-y-1.5">
          {top.map((s) => {
            const hot = s.pct > exposure.threshold_pct;
            return (
              <div key={s.sector} className="flex items-center gap-3 text-sm">
                <span className="w-44 shrink-0 truncate">{s.sector}</span>
                <div className="relative h-2.5 flex-1 rounded-full bg-muted">
                  <div className={cn("h-full rounded-full", hot ? "bg-amber-500" : "bg-zinc-400")} style={{ width: `${(s.pct / max) * 100}%` }} />
                  <div className="absolute top-[-3px] h-4 w-px bg-zinc-500" style={{ left: `${(exposure.threshold_pct / max) * 100}%` }} title="seuil d'alerte" />
                </div>
                <span className={cn("w-12 text-right tabular-nums", hot && "font-semibold text-amber-700")}>{s.pct.toFixed(0)} %</span>
                <span className="w-20 text-right text-xs text-muted-foreground">{s.models} modèle{s.models > 1 ? "s" : ""}</span>
              </div>
            );
          })}
        </div>
        <div className="flex flex-wrap gap-1.5 pt-1 text-xs">
          {exposure.symbols.slice(0, 10).map((s) => (
            <span key={s.symbol} className="rounded bg-muted px-1.5 py-0.5" title={`${s.label} — ${s.models.join(", ")}`}>
              <b>{s.symbol}</b> {s.pct.toFixed(0)} %{s.models.length > 1 ? ` · ${s.models.length} modèles` : ""}
            </span>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
