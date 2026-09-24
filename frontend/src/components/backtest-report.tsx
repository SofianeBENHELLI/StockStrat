"use client";

import { useMemo } from "react";
import { Info } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import EquityChart from "@/components/equity-chart";
import Money from "@/components/money";
import Stat from "@/components/stat";
import { cn } from "@/lib/utils";
import { PROFILE_META, pct, qty, usd, type BacktestRun, type ProfileKey } from "@/lib/lab";

const day = (iso: string | null) =>
  iso ? new Date(iso).toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" }) : "—";

export default function BacktestReport({ run, profile }: { run: BacktestRun; profile: ProfileKey }) {
  const s = run.summary;
  const color = PROFILE_META[profile].hex;
  const series = useMemo(
    () => [
      { name: "Modèle", values: run.series.equity, color, width: 2 as const },
      { name: "S&P 500", values: run.series.benchmark, color: "#71717a", dashed: true },
      { name: "Placebo", values: run.series.placebo, color: "#8b5cf6", dashed: true },
    ],
    [run, color]
  );
  const t = s.trades;
  const d = s.diagnostics;
  const reasons = Object.entries(s.exit_reasons);
  const maxReason = Math.max(1, ...reasons.map(([, n]) => n));

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Stat label={`Gain / perte sur ${usd(run.budget, { signed: false })}`} hint={`${pct(s.cagr_pct)} par an · ${pct(s.pnl_pct)} au total`}>
          <Money value={s.pnl_usd} />
        </Stat>
        <Stat label="Pire creux" hint={`${pct(s.episode_pct ?? s.max_drawdown_pct)} depuis le sommet · ${day(s.peak_day)} → ${day(s.trough_day)}${s.recovered_day ? "" : " · pas encore récupéré"}`}>
          <Money value={s.max_drawdown_usd} signed={false} />
        </Stat>
        <Stat label="Face au S&P 500" hint={`le S&P 500 a fait ${usd(s.benchmark.pnl_usd, { signed: true })}`}>
          <Money value={s.vs_benchmark_usd} />
        </Stat>
        <Stat label="Face au placebo" hint={`l'univers à parts égales, sans décision : ${usd(s.placebo.pnl_usd, { signed: true })}`}
          className={s.vs_placebo_usd > 0 ? "ring-emerald-500/30" : "ring-red-500/20"}>
          <Money value={s.vs_placebo_usd} />
        </Stat>
      </div>

      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
          <CardTitle>Valeur du portefeuille, {day(run.start)} → {day(run.end)}</CardTitle>
          <div className="flex items-center gap-4 text-xs text-muted-foreground">
            <Legend color={color} label="Modèle" />
            <Legend color="#71717a" label="S&P 500" dashed />
            <Legend color="#8b5cf6" label="Placebo" dashed />
          </div>
        </CardHeader>
        <CardContent>
          <EquityChart dates={run.series.dates} series={series} height={320} />
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader><CardTitle>Année par année</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Année</TableHead>
                  <TableHead className="text-right">Gain</TableHead>
                  <TableHead className="text-right">%</TableHead>
                  <TableHead className="text-right">S&P</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {s.yearly.map((y) => (
                  <TableRow key={y.year}>
                    <TableCell>{y.year}</TableCell>
                    <TableCell className="text-right"><Money value={y.pnl_usd} /></TableCell>
                    <TableCell className={cn("text-right tabular-nums", y.pnl_pct >= y.benchmark_pnl_pct ? "font-medium" : "")}>{pct(y.pnl_pct)}</TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">{pct(y.benchmark_pnl_pct)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Les trades</CardTitle></CardHeader>
          <CardContent className="space-y-1.5 text-sm">
            <Row k="Achats / ventes" v={`${t.n_buys} / ${t.n_sells}`} />
            <Row k="Ventes gagnantes" v={t.hit_rate_pct == null ? "—" : pct(t.hit_rate_pct, { signed: false })} />
            <Row k="Gain moyen / perte moyenne" v={<><Money value={t.avg_win_usd} /> / <Money value={t.avg_loss_usd} /></>} />
            <Row k="Gains ÷ pertes" v={t.profit_factor == null ? "—" : t.profit_factor.toFixed(2).replace(".", ",")} />
            <Row k="Détention moyenne" v={t.avg_hold_days == null ? "—" : `${t.avg_hold_days.toFixed(0)} séances`} />
            <Row k="Meilleur / pire trade" v={<><Money value={t.best_trade_usd} /> / <Money value={t.worst_trade_usd} /></>} />
            <Row k="Temps investi" v={pct(s.exposure_pct, { signed: false, digits: 0 })} />
            <div className="pt-3">
              <div className="mb-1.5 text-xs font-medium text-muted-foreground">Pourquoi il est sorti</div>
              {reasons.map(([k, n]) => (
                <div key={k} className="mb-1 flex items-center gap-2 text-xs">
                  <span className="w-40 shrink-0 truncate">{k}</span>
                  <span className="h-2 rounded-full" style={{ width: `${(n / maxReason) * 100}%`, background: color, opacity: 0.7 }} />
                  <span className="tabular-nums text-muted-foreground">{n}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>À lire avant d&apos;y croire</CardTitle></CardHeader>
          <CardContent className="space-y-3 text-sm">
            {d.months_scored ? (
              <div className={cn("rounded-lg p-3 text-sm ring-1", Math.abs(d.t_stat ?? 0) >= 2 ? "bg-emerald-500/5 ring-emerald-500/20" : "bg-amber-500/5 ring-amber-500/30")}>
                <div className="font-medium">
                  Le modèle prédit-il vraiment ? {Math.abs(d.t_stat ?? 0) >= 2 ? "Oui, un signal est détecté." : "Non établi."}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  Corrélation de rang moyenne entre prédiction et résultat réel : {(d.mean_rank_correlation ?? 0).toFixed(3).replace(".", ",")}
                  {" "}(t = {(d.t_stat ?? 0).toFixed(1).replace(".", ",")}, sur {d.months_scored} mois, {pct(d.positive_months_pct, { signed: false, digits: 0 })} de mois positifs).
                  {Math.abs(d.t_stat ?? 0) < 2 && " Sous |t| = 2, la performance ne peut pas être attribuée au modèle : elle vient de l'univers ou du hasard."}
                </p>
                {d.coefficients && Object.keys(d.coefficients).length > 0 && (
                  <div className="mt-2 space-y-0.5 text-xs">
                    <div className="text-muted-foreground">Ce que le modèle linéaire a appris en dernier :</div>
                    {Object.entries(d.coefficients).slice(0, 5).map(([k, v]) => (
                      <div key={k} className="flex justify-between"><span>{k}</span><span className={cn("tabular-nums", v >= 0 ? "text-emerald-700" : "text-red-700")}>{v > 0 ? "+" : ""}{v.toFixed(3)}</span></div>
                    ))}
                  </div>
                )}
              </div>
            ) : null}
            <Caveat>
              <b>Placebo</b> : l&apos;univers du profil acheté à parts égales et conservé, sans aucune décision. Un modèle qui ne
              le bat pas n&apos;apporte rien que l&apos;univers ne donnait déjà.
            </Caveat>
            {profile !== "stratege" ? (
              <Caveat>
                <b>Biais du survivant</b> : l&apos;univers est la liste des géants d&apos;aujourd&apos;hui. Sur le passé, il ne contient que
                des entreprises devenues gagnantes — tout ce qu&apos;on y pioche est flatté. C&apos;est pour ça que le placebo bat le S&P 500.
              </Caveat>
            ) : (
              <Caveat>
                <b>Choix des thèmes</b> : la liste des thèmes a été établie aujourd&apos;hui. Le moteur n&apos;invente rien, mais la liste
                elle-même profite du recul.
              </Caveat>
            )}
            <Caveat>
              Exécution à la clôture du jour de décision, {String(run.params.cost_bps ?? 5)} pb de coût à chaque achat et vente,
              actions fractionnées autorisées.
            </Caveat>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle>Derniers mouvements du backtest</CardTitle></CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Titre</TableHead>
                <TableHead>Sens</TableHead>
                <TableHead className="text-right">Qté</TableHead>
                <TableHead className="text-right">Prix</TableHead>
                <TableHead>Raison</TableHead>
                <TableHead className="text-right">Résultat</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {[...run.trades].reverse().slice(0, 25).map((tr, i) => (
                <TableRow key={i}>
                  <TableCell className="tabular-nums">{tr.day}</TableCell>
                  <TableCell><span className="font-medium">{tr.symbol}</span> {tr.label !== tr.symbol && <span className="text-muted-foreground">{tr.label}</span>}</TableCell>
                  <TableCell className={tr.side === "buy" ? "text-blue-700" : "text-amber-700"}>{tr.side === "buy" ? "Achat" : "Vente"}</TableCell>
                  <TableCell className="text-right tabular-nums">{qty(tr.qty)}</TableCell>
                  <TableCell className="text-right tabular-nums">{usd(tr.price, { signed: false, cents: true })}</TableCell>
                  <TableCell className="max-w-64 truncate text-muted-foreground" title={tr.reason}>{tr.reason}</TableCell>
                  <TableCell className="text-right">{tr.pnl == null ? "" : <Money value={tr.pnl} />}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-muted-foreground">{k}</span>
      <span className="tabular-nums">{v}</span>
    </div>
  );
}

function Caveat({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex gap-2 text-xs leading-relaxed text-muted-foreground">
      <Info className="mt-0.5 size-3.5 shrink-0" />
      <span>{children}</span>
    </p>
  );
}

function Legend({ color, label, dashed }: { color: string; label: string; dashed?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <svg width="18" height="6" aria-hidden><line x1="0" y1="3" x2="18" y2="3" stroke={color} strokeWidth="2" strokeDasharray={dashed ? "3 3" : undefined} /></svg>
      {label}
    </span>
  );
}
