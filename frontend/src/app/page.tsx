"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Compass, Dices, type LucideIcon, Sigma } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, ApiError, fmtPct, pnlColor } from "@/lib/api";

type EngineKey = "casino" | "ml" | "economist";

type EngineMeta = {
  nickname: string;
  label: string;
  description: string;
  Icon: LucideIcon;
  iconWrap: string;
  ring: string;
};

const ENGINE_META: Record<EngineKey, EngineMeta> = {
  casino: {
    nickname: "Le Flambeur",
    label: "Casino",
    description: "Opportuniste, court terme, payoff asymétrique — parie sur l'amplitude ou la direction d'un mouvement déclenché par un catalyseur.",
    Icon: Dices,
    iconWrap: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
    ring: "hover:ring-amber-500/30",
  },
  ml: {
    nickname: "Le Matheux",
    label: "Machine Learning",
    description: "Adaptatif — modèle Ridge entraîné et validé en walk-forward sur le momentum de prix et le régime macro.",
    Icon: Sigma,
    iconWrap: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
    ring: "hover:ring-blue-500/30",
  },
  economist: {
    nickname: "Le Stratège",
    label: "The Economist",
    description: "Macro, thématique, moyen/long terme — sélectionne les bénéficiaires de grandes thèses sectorielles.",
    Icon: Compass,
    iconWrap: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    ring: "hover:ring-emerald-500/30",
  },
};

type LeaderboardRow = {
  rank: number;
  variant_id: number;
  name: string;
  engine: string;
  variant_key: string;
  status: string;
  generation: number;
  equity: number;
  total_pnl_pct: number;
  sharpe: number;
  sortino: number;
  max_drawdown_pct: number;
  hit_rate_pct: number | null;
  profit_factor: number | null;
  n_trades: number;
  n_closed_trades: number;
};

type SystemState = { kill_switch_engaged: boolean; kill_switch_reason: string };
type CycleResult = { id: number; status: string; summary: string };

export default function Dashboard() {
  const [rows, setRows] = useState<LeaderboardRow[] | null>(null);
  const [system, setSystem] = useState<SystemState | null>(null);
  const [name, setName] = useState("");
  const [engine, setEngine] = useState("manual");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"seed" | "cycle" | null>(null);
  const [lastCycle, setLastCycle] = useState<CycleResult | null>(null);

  async function refresh() {
    try {
      const [r, s] = await Promise.all([
        api<LeaderboardRow[]>("/api/tournament/leaderboard"),
        api<SystemState>("/api/system/state"),
      ]);
      setRows(r);
      setSystem(s);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Impossible de joindre l'API backend.");
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function createVariant() {
    if (!name.trim()) return;
    await api("/api/variants", { method: "POST", body: JSON.stringify({ name, engine, initial_cash: 100_000 }) });
    setName("");
    refresh();
  }

  async function toggleKillSwitch() {
    if (!system) return;
    const engaged = !system.kill_switch_engaged;
    await api("/api/system/kill-switch", {
      method: "POST",
      body: JSON.stringify({ engaged, reason: engaged ? "Arrêt manuel depuis le dashboard" : "" }),
    });
    refresh();
  }

  async function seedRoster() {
    setBusy("seed");
    try {
      const r = await api<{ created: number }>("/api/tournament/seed", { method: "POST" });
      setError(r.created === 0 ? null : null);
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Échec du seed.");
    } finally {
      setBusy(null);
    }
  }

  async function runCycle() {
    setBusy("cycle");
    try {
      const r = await api<CycleResult>("/api/tournament/cycle/run", { method: "POST" });
      setLastCycle(r);
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Échec du cycle.");
    } finally {
      setBusy(null);
    }
  }

  const rowClass = (r: LeaderboardRow) => {
    if (r.status === "killed") return "opacity-50";
    if (r.n_trades === 0) return "";
    return r.total_pnl_pct >= 1 ? "bg-emerald-500/5" : r.total_pnl_pct <= -1 ? "bg-red-500/5" : "";
  };

  function engineStats(engine: EngineKey) {
    if (!rows) return null;
    const list = rows.filter((r) => r.engine === engine);
    if (list.length === 0) return null;
    const active = list.filter((r) => r.status === "active").length;
    const avgPnl = list.reduce((sum, r) => sum + r.total_pnl_pct, 0) / list.length;
    return { total: list.length, active, avgPnl };
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Tournoi de stratégies</h1>
          <p className="text-sm text-muted-foreground">
            {rows?.length ?? "…"} variantes, cycle propose → exécute → score → tue → scale → génère. Classement ajusté du risque.
          </p>
        </div>
        {system && (
          <Button variant={system.kill_switch_engaged ? "destructive" : "outline"} onClick={toggleKillSwitch}>
            {system.kill_switch_engaged ? "Kill-switch: ENGAGÉ — Réactiver" : "Kill-switch global"}
          </Button>
        )}
      </div>

      {error && (
        <Card className="border-destructive/40">
          <CardContent className="text-destructive text-sm py-2">{error}</CardContent>
        </Card>
      )}

      <div>
        <h2 className="text-sm font-medium text-muted-foreground mb-3">Les 3 moteurs de stratégie</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {(Object.keys(ENGINE_META) as EngineKey[]).map((e) => {
            const meta = ENGINE_META[e];
            const stats = engineStats(e);
            return (
              <Link key={e} href={`/strategies/${e}`}>
                <Card className={`h-full transition-all hover:shadow-md hover:-translate-y-0.5 ${meta.ring}`}>
                  <CardHeader>
                    <div className="flex items-start gap-3">
                      <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl ${meta.iconWrap}`}>
                        <meta.Icon className="h-6 w-6" strokeWidth={2} />
                      </div>
                      <div className="min-w-0">
                        <CardTitle className="leading-tight">{meta.nickname}</CardTitle>
                        <p className="text-xs text-muted-foreground mt-0.5">{meta.label}</p>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent className="flex flex-col gap-3">
                    <p className="text-xs text-muted-foreground">{meta.description}</p>
                    <div className="flex items-center gap-2 text-xs pt-2 border-t border-border">
                      {stats ? (
                        <>
                          <span className="text-muted-foreground">{stats.active}/{stats.total} actives</span>
                          <span className="text-muted-foreground">·</span>
                          <span className={pnlColor(stats.avgPnl)}>P&amp;L moy. {fmtPct(stats.avgPnl, 2)}</span>
                        </>
                      ) : (
                        <span className="text-muted-foreground">Pas encore de variante amorcée</span>
                      )}
                    </div>
                  </CardContent>
                </Card>
              </Link>
            );
          })}
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Cycle du tournoi</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          <div className="flex gap-2">
            <Button variant="outline" onClick={seedRoster} disabled={busy !== null}>
              {busy === "seed" ? "…" : "Amorcer le roster (14 variantes)"}
            </Button>
            <Button onClick={runCycle} disabled={busy !== null}>
              {busy === "cycle" ? "Cycle en cours…" : "Lancer un cycle maintenant"}
            </Button>
          </div>
          {lastCycle && <p className="text-xs text-muted-foreground">Dernier cycle #{lastCycle.id} : {lastCycle.summary}</p>}
          <p className="text-xs text-muted-foreground">
            Un cycle propose jusqu&apos;à 2 idées par variante active, les exécute en paper, prend un snapshot, puis
            tue (P&amp;L ≤ -8%) ou scale (+10% de cash, P&amp;L ≥ +5%) les variantes ayant au moins 3 trades clôturés —
            en dessous de ce seuil, pas assez de données pour juger.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Classement ({rows?.length ?? 0})</CardTitle>
        </CardHeader>
        <CardContent>
          {rows === null ? (
            <p className="text-sm text-muted-foreground">Chargement…</p>
          ) : rows.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Aucune variante. Cliquez « Amorcer le roster » ci-dessus, ou créez-en une manuellement plus bas.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>#</TableHead>
                  <TableHead>Variante</TableHead>
                  <TableHead>Moteur</TableHead>
                  <TableHead>Statut</TableHead>
                  <TableHead>P&amp;L</TableHead>
                  <TableHead>Sharpe</TableHead>
                  <TableHead>Max DD</TableHead>
                  <TableHead>Hit rate</TableHead>
                  <TableHead>Trades</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={r.variant_id} className={rowClass(r)}>
                    <TableCell className="text-muted-foreground">{r.rank}</TableCell>
                    <TableCell className="font-medium">
                      {r.name}
                      {r.generation > 0 && <Badge variant="outline" className="ml-1.5">gen {r.generation}</Badge>}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">{r.engine}-{r.variant_key}</Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={r.status === "active" ? "success" : "outline"}>{r.status}</Badge>
                    </TableCell>
                    <TableCell className={pnlColor(r.total_pnl_pct)}>{fmtPct(r.total_pnl_pct, 3)}</TableCell>
                    <TableCell>{r.sharpe.toFixed(2)}</TableCell>
                    <TableCell>{r.max_drawdown_pct.toFixed(2)}%</TableCell>
                    <TableCell>{r.hit_rate_pct != null ? `${r.hit_rate_pct.toFixed(0)}%` : "—"}</TableCell>
                    <TableCell>{r.n_trades}</TableCell>
                    <TableCell>
                      <Link href={`/variants/${r.variant_id}`}>
                        <Button variant="outline" size="sm">Ouvrir</Button>
                      </Link>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Nouvelle variante manuelle</CardTitle>
        </CardHeader>
        <CardContent className="flex gap-2">
          <Input placeholder="ex. Test manuel" value={name} onChange={(e) => setName(e.target.value)} />
          <select
            className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm"
            value={engine}
            onChange={(e) => setEngine(e.target.value)}
          >
            <option value="manual">manual</option>
            <option value="casino">casino</option>
            <option value="ml">ml</option>
            <option value="economist">economist</option>
          </select>
          <Button onClick={createVariant}>Créer</Button>
        </CardContent>
      </Card>
    </div>
  );
}
