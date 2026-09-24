"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import type { Clock } from "@/lib/lab";

function until(iso: string) {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "maintenant";
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  return h > 0 ? `${h} h ${String(m).padStart(2, "0")}` : `${m} min`;
}

/** Wall Street's state, in the viewer's local time, ticking. */
export default function MarketClock({ clock, className }: { clock?: Clock; className?: string }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 20_000);
    return () => clearInterval(t);
  }, []);
  if (!clock) return null;
  if (clock.error) {
    return <span className={cn("text-xs text-red-600", className)}>Horloge indisponible</span>;
  }
  const open = clock.is_open;
  const target = open ? clock.next_close : clock.next_open;
  const local = target
    ? new Date(target).toLocaleString("fr-FR", { weekday: "short", hour: "2-digit", minute: "2-digit" })
    : "";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium ring-1",
        open ? "bg-emerald-500/10 text-emerald-700 ring-emerald-500/30" : "bg-zinc-500/10 text-zinc-600 ring-zinc-500/20",
        className
      )}
    >
      <span className={cn("size-2 rounded-full", open ? "bg-emerald-500 animate-pulse" : "bg-zinc-400")} />
      {open ? "Wall Street ouverte" : "Wall Street fermée"}
      {target && (
        <span className="font-normal opacity-80">
          · {open ? "ferme" : "ouvre"} dans {until(target)} ({local})
        </span>
      )}
    </span>
  );
}
