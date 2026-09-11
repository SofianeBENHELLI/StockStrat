"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import EquityChart from "@/components/equity-chart";
import { api, ApiError, fmtPct, pnlColor } from "@/lib/api";

type BacktestRow = {
  variant_id: string;
  name: string;
  engine: string;
  variant_key: string;
  status: string;
  final_equity: number;
  total_pnl_pct: number;
  max_drawdown_pct: number;
  sharpe: number;
  sortino: number;
  n_fills: number;
  hit_rate_pct: number | null;
  profit_factor: number | null;
  equity_history: [string, number][];
};

type BacktestResult = {
  years: number;
  rebalance_every_days: number;
  n_trading_days: number;
  n_rebalances: number;
  universe_size: number;
  ml_insample_caveat: string;
  results: BacktestRow[];
};

type StoredRun = { result: BacktestResult; ranAt: string };

const YEARS_OPTIONS = [1, 2, 3, 5];
const REBALANCE_OPTIONS = [10, 15, 20, 30];
const ENGINE_LABELS: Record<string, string> = {
  casino: "Le Flambeur (Casino)",
  ml: "Le Matheux (Machine Learning)",
  economist: "Le Stratège (Economist)",
};
const PALETTE = ["#2563eb", "#16a34a", "#dc2626", "#d97706", "#7c3aed"];
const STORAGE_KEY = "st_backtest_last_result";

function loadStoredRun(): StoredRun | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as StoredRun) : null;
  } catch {
    return null; // private browsing, quota, corrupted JSON — never crash the page over this
  }
}

function saveStoredRun(run: StoredRun) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(run));
  } catch {
    // best effort — e.g. quota exceeded on a very large 5-year run; losing the
    // cache is annoying, not breaking, so just drop it silently
  }
}

export default function BacktestPage() {
  const [years, setYears] = useState(1);
  const [rebalanceEveryDays, setRebalanceEveryDays] = useState(10);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [ranAt, setRanAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const stored = loadStoredRun();
    if (stored) {
      setResult(stored.result);
      setRanAt(stored.ranAt);
    }
  }, []);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const r = await api<BacktestResult>("/api/backtest/run", {
        method: "POST",
        body: JSON.stringify({ years, rebalance_every_days: rebalanceEveryDays }),
      });
      const now = new Date().toISOString();
      setResult(r);
      setRanAt(now);
      saveStoredRun({ result: r, ranAt: now });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Échec du backtest.");
    } finally {
      setBusy(false);
    }
  }

  const byEngine = result
    ? result.results.reduce<Record<string, BacktestRow[]>>((acc, r) => {
        (acc[r.engine] ??= []).push(r);
        return acc;
      }, {})
    : {};

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">Backtest historique</h1>
        <p className="text-sm text-muted-foreground">
          Rejoue les 3 moteurs + la logique tue/renforce du tournoi sur des années de données passées, en un seul
          appel — plutôt que d&apos;attendre des mois de cycles en direct pour un échantillon statistiquement
          exploitable. Achat seul, comme le cycle en direct (aucune vente automatique dans cette appli) : le hit
          rate / profit factor restent donc vides, honnêtement, ici comme en direct.
        </p>
      </div>

      {error && (
        <Card className="border-destructive/40">
          <CardContent className="text-destructive text-sm py-2">{error}</CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Paramètres</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex items-center gap-4 flex-wrap">
            <label className="text-sm flex items-center gap-2">
              Période
              <select
                className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm"
                value={years}
                onChange={(e) => setYears(Number(e.target.value))}
                disabled={busy}
              >
                {YEARS_OPTIONS.map((y) => (
                  <option key={y} value={y}>{y} an{y > 1 ? "s" : ""}</option>
                ))}
              </select>
            </label>
            <label className="text-sm flex items-center gap-2">
              Rééquilibrage tous les
              <select
                className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm"
                value={rebalanceEveryDays}
                onChange={(e) => setRebalanceEveryDays(Number(e.target.value))}
                disabled={busy}
              >
                {REBALANCE_OPTIONS.map((d) => (
                  <option key={d} value={d}>{d} jours</option>
                ))}
              </select>
            </label>
            <Button onClick={run} disabled={busy}>
              {busy ? "Backtest en cours…" : "Lancer le backtest"}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            ~3 minutes mesurées pour 1 an / rééquilibrage tous les 10 jours (14 variantes, univers d&apos;environ 130
            titres) — proportionnellement plus long pour une période plus longue ou un rééquilibrage plus fréquent.
            La page reste bloquée pendant le calcul ; le résultat est ensuite gardé (navigateur local) même si tu
            changes de page, donc pas besoin de rester sur cet onglet en attendant.
          </p>
        </CardContent>
      </Card>

      {result && (
        <>
          {ranAt && (
            <p className="text-xs text-muted-foreground">
              Résultat du {new Date(ranAt).toLocaleString()} — gardé localement dans ce navigateur, pas
              nécessairement le dernier calcul possible.
            </p>
          )}

          <Card className="border-amber-500/40 bg-amber-500/5">
            <CardContent className="text-sm py-3">
              <span className="font-medium">Le Matheux (ML) a un avantage déloyal ici : </span>
              {result.ml_insample_caveat}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>
                Résultats ({result.results.length} variantes · {result.n_trading_days} jours de bourse ·{" "}
                {result.n_rebalances} rééquilibrages · univers de {result.universe_size} titres)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>#</TableHead>
                    <TableHead>Variante</TableHead>
                    <TableHead>Moteur</TableHead>
                    <TableHead>Statut</TableHead>
                    <TableHead>P&amp;L</TableHead>
                    <TableHead>Sharpe</TableHead>
                    <TableHead>Sortino</TableHead>
                    <TableHead>Max DD</TableHead>
                    <TableHead>Achats</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {result.results.map((r, i) => (
                    <TableRow key={r.variant_id} className={r.status === "killed" ? "opacity-50" : ""}>
                      <TableCell className="text-muted-foreground">{i + 1}</TableCell>
                      <TableCell className="font-medium">{r.name}</TableCell>
                      <TableCell>
                        <Badge variant="secondary">{r.engine}-{r.variant_key}</Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant={r.status === "active" ? "success" : "outline"}>{r.status}</Badge>
                      </TableCell>
                      <TableCell className={pnlColor(r.total_pnl_pct)}>{fmtPct(r.total_pnl_pct, 2)}</TableCell>
                      <TableCell>{r.sharpe.toFixed(2)}</TableCell>
                      <TableCell>{r.sortino.toFixed(2)}</TableCell>
                      <TableCell>{r.max_drawdown_pct.toFixed(2)}%</TableCell>
                      <TableCell>{r.n_fills}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          {Object.entries(byEngine).map(([engine, rows]) => (
            <Card key={engine}>
              <CardHeader>
                <CardTitle className="text-base">{ENGINE_LABELS[engine] ?? engine}</CardTitle>
              </CardHeader>
              <CardContent>
                <EquityChart
                  dates={rows[0]?.equity_history.map(([d]) => d) ?? []}
                  series={rows.map((r, i) => ({
                    name: r.name,
                    values: r.equity_history.map(([, v]) => v),
                    color: PALETTE[i % PALETTE.length],
                  }))}
                />
              </CardContent>
            </Card>
          ))}
        </>
      )}
    </div>
  );
}
