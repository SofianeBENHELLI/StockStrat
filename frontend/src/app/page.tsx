"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ArrowRight, CheckCircle2, CircleAlert, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import Journal from "@/components/journal";
import MarketClock from "@/components/market-clock";
import Money from "@/components/money";
import ProfileChip from "@/components/profile-chip";
import Sparkline from "@/components/sparkline";
import Stat from "@/components/stat";
import { api, ApiError } from "@/lib/api";
import { PROFILE_META, PROFILE_ORDER, pct, usd, type Model, type Overview } from "@/lib/lab";

export default function TradingFloor() {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api<Overview>("/api/lab/overview"));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "API injoignable — le backend tourne-t-il sur le port 8001 ?");
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 15_000);
    return () => clearInterval(t);
  }, [load]);

  async function pollNow() {
    setBusy(true);
    try {
      await api("/api/lab/monitor/run-once", { method: "POST" });
      await load();
    } finally {
      setBusy(false);
    }
  }

  if (!data) {
    return <p className="text-sm text-muted-foreground">{error ?? "Chargement de la salle des marchés…"}</p>;
  }

  const live = data.models.filter((m) => m.stage === "paper");
  const candidates = data.models
    .filter((m) => m.stage === "lab" && m.backtest)
    .sort((a, b) => (b.backtest!.pnl_usd ?? 0) - (a.backtest!.pnl_usd ?? 0))
    .slice(0, 3);
  const recon = data.monitor.reconciliation;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Salle des marchés</h1>
          <p className="text-sm text-muted-foreground">
            Les modèles déployés tradent seuls sur Alpaca paper, chacun avec son budget. Ils décident chaque jour
            peu avant la clôture de Wall Street.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <MarketClock clock={data.clock} />
          <Button variant="outline" size="sm" onClick={pollNow} disabled={busy} title="Sonder les ordres et marquer au marché maintenant">
            <RefreshCw className={busy ? "size-3.5 animate-spin" : "size-3.5"} /> Actualiser
          </Button>
        </div>
      </header>

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Capital déployé" hint={`plafond ${usd(data.totals.cap, { signed: false })} · jamais de marge`}>
          {usd(data.totals.allocated, { signed: false })}
        </Stat>
        <Stat label="Valeur des portefeuilles" hint={`${live.length} modèle${live.length > 1 ? "s" : ""} en paper`}>
          {usd(data.totals.equity, { signed: false })}
        </Stat>
        <Stat label="Gain / perte total">
          <Money value={data.totals.pnl_usd} />
        </Stat>
        <Stat label="Aujourd'hui" hint={recon?.ok === false ? undefined : "variation depuis la dernière clôture"}>
          <Money value={data.totals.today_usd} />
        </Stat>
      </section>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        {recon?.ok ? (
          <span className="inline-flex items-center gap-1 text-emerald-700">
            <CheckCircle2 className="size-3.5" /> Livres réconciliés avec Alpaca — {recon.detail}
          </span>
        ) : recon?.detail ? (
          <span className="inline-flex items-center gap-1 text-red-600">
            <CircleAlert className="size-3.5" /> Réconciliation : {recon.detail}
          </span>
        ) : null}
        <span className="text-muted-foreground">
          · boucle {data.monitor.running ? "active" : "arrêtée"}, {data.monitor.passes} passages
        </span>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <div className="space-y-6">
          {live.length === 0 ? (
            <Card>
              <CardHeader>
                <CardTitle>Aucun modèle ne trade encore</CardTitle>
                <p className="text-sm text-muted-foreground">
                  Les modèles du labo sont backtestés sur 10 ans de données réelles. Déployez les plus prometteurs :
                  chacun reçoit son propre budget et commence à trader sur Alpaca paper.
                </p>
              </CardHeader>
              <CardContent className="space-y-3">
                {candidates.map((m) => (
                  <CandidateRow key={m.id} m={m} />
                ))}
                <Link href="/backtest" className="inline-flex items-center gap-1 text-sm font-medium hover:underline">
                  Voir le classement complet <ArrowRight className="size-3.5" />
                </Link>
              </CardContent>
            </Card>
          ) : (
            PROFILE_ORDER.map((key) => {
              const models = live.filter((m) => m.profile === key);
              if (!models.length) return null;
              return (
                <section key={key} className="space-y-3">
                  <div className="flex items-center gap-2">
                    <ProfileChip profile={key} />
                    <span className="text-xs text-muted-foreground">{models[0].cadence === "daily" ? "décide chaque jour" : "rééquilibre chaque mois, surveille chaque jour"}</span>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {models.map((m) => (
                      <LiveCard key={m.id} m={m} />
                    ))}
                  </div>
                </section>
              );
            })
          )}
        </div>

        <Card className="self-start">
          <CardHeader>
            <CardTitle>Journal de bord</CardTitle>
          </CardHeader>
          <CardContent className="max-h-[70vh] overflow-y-auto">
            <Journal events={data.events} empty="Aucun événement. Déployez un modèle pour démarrer." />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function LiveCard({ m }: { m: Model }) {
  const p = m.paper!;
  const meta = PROFILE_META[m.profile];
  return (
    <Link href={`/paper/${m.id}`} className={`group block rounded-xl bg-card p-4 ring-1 ring-foreground/10 transition hover:ring-2 ${meta.ring}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-medium">{m.name}</div>
          <div className="text-xs text-muted-foreground">
            {p.positions} position{p.positions > 1 ? "s" : ""}
            {p.open_orders ? ` · ${p.open_orders} ordre${p.open_orders > 1 ? "s" : ""} en attente` : ""}
          </div>
        </div>
        <Sparkline values={p.sparkline} baseline={m.budget} width={90} height={30} />
      </div>
      <div className="mt-3 flex items-end justify-between">
        <div>
          <div className="text-xl font-semibold tabular-nums">{usd(p.equity, { signed: false })}</div>
          <div className="text-xs text-muted-foreground">sur {usd(m.budget, { signed: false })} de départ</div>
        </div>
        <div className="text-right">
          <Money value={p.pnl_usd} className="text-base font-semibold" />
          <div className="text-xs text-muted-foreground">
            aujourd&apos;hui <Money value={p.today_usd} /> · creux <Money value={p.max_drawdown_usd} signed={false} />
          </div>
        </div>
      </div>
    </Link>
  );
}

function CandidateRow({ m }: { m: Model }) {
  const b = m.backtest!;
  return (
    <Link href={`/lab/${m.id}`} className="flex items-center justify-between gap-3 rounded-lg p-2 ring-1 ring-foreground/10 hover:bg-muted/50">
      <div className="flex min-w-0 items-center gap-2">
        <ProfileChip profile={m.profile} />
        <span className="truncate text-sm font-medium">{m.name}</span>
      </div>
      <div className="flex shrink-0 items-center gap-4 text-sm">
        <span>
          <Money value={b.pnl_usd} /> <span className="text-xs text-muted-foreground">depuis {b.start.slice(0, 4)}</span>
        </span>
        <span className="text-xs text-muted-foreground">creux <Money value={b.max_drawdown_usd} signed={false} /></span>
        <span className="text-xs text-muted-foreground">{pct(b.cagr_pct)}/an</span>
      </div>
    </Link>
  );
}
