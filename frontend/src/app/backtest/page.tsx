"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Loader2, Play, Rocket } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import Money from "@/components/money";
import ProfileChip from "@/components/profile-chip";
import Sparkline from "@/components/sparkline";
import { api, ApiError } from "@/lib/api";
import { PROFILE_META, pct, usd, type Model, type ProfileKey } from "@/lib/lab";

type SortKey = "pnl" | "ratio" | "placebo";

export default function Ranking() {
  const [models, setModels] = useState<Model[] | null>(null);
  const [start, setStart] = useState("2019-01-02");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>("pnl");
  const [filter, setFilter] = useState<ProfileKey | "all">("all");

  async function load() {
    setModels(await api<Model[]>("/api/lab/models"));
  }
  useEffect(() => {
    load().catch((e) => setError(e instanceof ApiError ? e.message : "API injoignable."));
  }, []);

  async function runAll() {
    setBusy("all");
    setError(null);
    try {
      await api("/api/lab/backtest-all", { method: "POST", body: JSON.stringify({ start }) });
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function deploy(m: Model) {
    if (!confirm(`Déployer « ${m.name} » en paper avec ${usd(m.budget, { signed: false })} ?`)) return;
    setBusy(`deploy-${m.id}`);
    setError(null);
    try {
      await api(`/api/lab/models/${m.id}/promote`, { method: "POST", body: JSON.stringify({ budget: m.budget }) });
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const rows = useMemo(() => {
    const list = (models ?? []).filter((m) => m.backtest && (filter === "all" || m.profile === filter));
    const key = (m: Model) =>
      sort === "pnl" ? m.backtest!.pnl_usd : sort === "placebo" ? m.backtest!.vs_placebo_usd : m.backtest!.return_over_drawdown ?? -1e9;
    return list.sort((a, b) => key(b) - key(a));
  }, [models, sort, filter]);

  const ref = rows[0]?.backtest;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Classement</h1>
          <p className="max-w-3xl text-sm text-muted-foreground">
            Tous les modèles, sur la même période et le même budget, classés en dollars. Deux étalons à battre : le
            S&P 500, et le <b>placebo</b> — l&apos;univers du modèle acheté à parts égales, sans aucune décision.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select value={start} onChange={(e) => setStart(e.target.value)} className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm">
            <option value="2016-01-04">Depuis 2016</option>
            <option value="2019-01-02">Depuis 2019</option>
            <option value="2022-01-03">Depuis 2022</option>
            <option value="2024-01-02">Depuis 2024</option>
          </select>
          <Button onClick={runAll} disabled={!!busy}>
            {busy === "all" ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
            {busy === "all" ? "9 backtests en cours…" : "Tout backtester"}
          </Button>
        </div>
      </header>

      {error && <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-700">{error}</p>}

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground">Trier par</span>
        {([["pnl", "Gain en $"], ["ratio", "Gain ÷ pire creux"], ["placebo", "Écart au placebo"]] as [SortKey, string][]).map(([k, l]) => (
          <Button key={k} size="sm" variant={sort === k ? "default" : "outline"} onClick={() => setSort(k)}>{l}</Button>
        ))}
        <span className="ml-4 text-muted-foreground">Profil</span>
        {(["all", "flambeur", "matheux", "stratege"] as const).map((k) => (
          <Button key={k} size="sm" variant={filter === k ? "default" : "outline"} onClick={() => setFilter(k)}>
            {k === "all" ? "Tous" : k === "flambeur" ? "Flambeur" : k === "matheux" ? "Matheux" : "Stratège"}
          </Button>
        ))}
      </div>

      <Card>
        <CardContent className="px-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-4">#</TableHead>
                <TableHead>Modèle</TableHead>
                <TableHead className="text-right">Gain</TableHead>
                <TableHead className="text-right">Pire creux</TableHead>
                <TableHead className="text-right" title="Gain divisé par le pire creux : combien on gagne par dollar de risque subi">Gain ÷ creux</TableHead>
                <TableHead className="text-right">vs S&P 500</TableHead>
                <TableHead className="text-right">vs placebo</TableHead>
                <TableHead className="text-right">Ventes</TableHead>
                <TableHead>Trajectoire</TableHead>
                <TableHead className="pr-4 text-right">Paper</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((m, i) => {
                const b = m.backtest!;
                return (
                  <TableRow key={m.id}>
                    <TableCell className="pl-4 tabular-nums text-muted-foreground">{i + 1}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <ProfileChip profile={m.profile} />
                        <Link href={`/lab/${m.id}`} className="font-medium hover:underline">{m.name}</Link>
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {b.start.slice(0, 4)}→{b.end.slice(0, 4)} · {pct(b.cagr_pct)}/an · investi {pct(b.exposure_pct, { signed: false, digits: 0 })} du temps
                      </div>
                    </TableCell>
                    <TableCell className="text-right"><Money value={b.pnl_usd} className="font-semibold" /></TableCell>
                    <TableCell className="text-right"><Money value={b.max_drawdown_usd} signed={false} /></TableCell>
                    <TableCell className="text-right tabular-nums">{b.return_over_drawdown == null ? "—" : b.return_over_drawdown.toFixed(1).replace(".", ",")}</TableCell>
                    <TableCell className="text-right"><Money value={b.vs_benchmark_usd} /></TableCell>
                    <TableCell className="text-right"><Money value={b.vs_placebo_usd} /></TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">{b.n_sells ?? "—"}</TableCell>
                    <TableCell><Sparkline values={b.spark} baseline={b.budget} width={110} height={30} color={PROFILE_META[m.profile].hex} /></TableCell>
                    <TableCell className="pr-4 text-right">
                      {m.stage === "paper" ? (
                        <Link href={`/paper/${m.id}`}><Badge variant="success">En paper</Badge></Link>
                      ) : m.stage === "retired" ? (
                        <Badge variant="outline">Retiré</Badge>
                      ) : (
                        <Button size="sm" onClick={() => deploy(m)} disabled={!!busy}>
                          {busy === `deploy-${m.id}` ? <Loader2 className="size-3.5 animate-spin" /> : <Rocket className="size-3.5" />}
                          Déployer
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      {ref && (
        <p className="text-xs text-muted-foreground">
          Sur la même période, le S&P 500 a fait {usd(ref.benchmark_pnl_usd)} pour {usd(ref.budget, { signed: false })} investis.
          Les placebos diffèrent par profil (univers actions pour le Flambeur et le Matheux, univers d&apos;ETF thématiques pour le Stratège).
        </p>
      )}
    </div>
  );
}
