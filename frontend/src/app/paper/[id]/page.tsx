"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, FlaskConical, Loader2, Play, Power } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import EquityChart from "@/components/equity-chart";
import Journal from "@/components/journal";
import Money from "@/components/money";
import ProfileChip from "@/components/profile-chip";
import Stat from "@/components/stat";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { PROFILE_META, STAGE_LABEL, pct, qty, usd, type Feedback, type PaperDetail } from "@/lib/lab";

const STATUS: Record<string, { label: string; cls: string }> = {
  filled: { label: "exécuté", cls: "text-emerald-700" },
  partial_fill: { label: "partiel", cls: "text-emerald-700" },
  open: { label: "en attente", cls: "text-blue-700" },
  new: { label: "posé", cls: "text-violet-700" },
  proposed: { label: "proposé", cls: "text-muted-foreground" },
  rejected: { label: "refusé", cls: "text-red-700" },
  cancelled: { label: "annulé", cls: "text-muted-foreground" },
};

function FeedbackCard({ f, modelId }: { f: Feedback; modelId: number }) {
  const s = f.costs?.in_session;
  const o = f.costs?.overnight;
  const tone = { action: "bg-violet-500/10 text-violet-900", warning: "bg-amber-500/10 text-amber-900", info: "bg-muted text-muted-foreground" };
  return (
    <Card>
      <CardHeader>
        <CardTitle>Ce que le réel apprend au backtest</CardTitle>
        <p className="text-xs text-muted-foreground">
          Le coût d&apos;exécution est l&apos;écart entre le prix sur lequel l&apos;ordre a été calculé et le prix obtenu. Les ordres
          passés hors séance sont comptés à part : leur écart contient le mouvement de la nuit.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Coût supposé au backtest">{f.assumed_cost_bps} pb</Stat>
          <Stat label="Coût mesuré en séance" hint={s && s.n ? `${s.n} ordre${s.n > 1 ? "s" : ""} · ${usd(s.cost_usd, { signed: false, cents: true })} au total` : "aucun ordre en séance encore"}>
            {s?.weighted_bps == null ? "—" : `${s.weighted_bps.toFixed(1).replace(".", ",")} pb`}
          </Stat>
          <Stat label="Écart d'ouverture (hors séance)" hint={o && o.n ? `${o.n} ordre${o.n > 1 ? "s" : ""} — mouvement de marché, pas un coût` : "—"}>
            {o?.weighted_bps == null ? "—" : `${o.weighted_bps.toFixed(1).replace(".", ",")} pb`}
          </Stat>
          <Stat label="Réel − backtest rejoué" hint={f.gap ? `dont exécution ${usd(f.gap.execution_usd, { signed: true })}, reste ${usd(f.gap.other_usd, { signed: true })}` : "après deux séances"}>
            {f.gap ? <Money value={f.gap.gap_usd} /> : "—"}
          </Stat>
        </div>
        {f.suggestions?.map((sg, i) => (
          <p key={i} className={cn("rounded-lg px-3 py-2 text-sm", tone[sg.level])}>
            {sg.text}
            {sg.suggested_cost_bps != null && (
              <> <Link href={`/lab/${modelId}`} className="font-medium underline">Ouvrir au labo</Link></>
            )}
          </p>
        ))}
      </CardContent>
    </Card>
  );
}

function nextMonth() {
  const d = new Date();
  return new Date(d.getFullYear(), d.getMonth() + 1, 1).toLocaleDateString("fr-FR", { month: "long" });
}

export default function PaperModel() {
  const { id } = useParams<{ id: string }>();
  const [d, setD] = useState<PaperDetail | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setD(await api<PaperDetail>(`/api/lab/models/${id}/paper`));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Modèle introuvable.");
    }
  }, [id]);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 30_000);
    return () => clearInterval(t);
  }, [load]);

  async function act(label: string, path: string, confirmText?: string) {
    if (confirmText && !confirm(confirmText)) return;
    setBusy(label);
    setError(null);
    try {
      await api(path, { method: "POST" });
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const chart = useMemo(() => {
    if (!d) return null;
    const m = d.model;
    const color = PROFILE_META[m.profile].hex;
    const dates = Array.from(new Set([...(d.curve.dates ?? []), ...(d.replay.dates ?? [])])).sort();
    const at = (ds: string[] | undefined, vs: number[] | undefined) => {
      const map = new Map((ds ?? []).map((x, i) => [x, vs![i]]));
      return dates.map((x) => map.get(x) ?? NaN);
    };
    const series = [{ name: "Paper réel", values: at(d.curve.dates, d.curve.equity), color, width: 2 as const }];
    if (d.replay.available) series.push({ name: "Backtest rejoué", values: at(d.replay.dates, d.replay.equity), color: "#71717a", dashed: true } as never);
    return { dates, series };
  }, [d]);

  if (!d) return <p className="text-sm text-muted-foreground">{error ?? "Chargement…"}</p>;
  const m = d.model;
  const p = m.paper!;
  const now = d.now;
  const pending = now.orders ?? [];

  return (
    <div className="space-y-6">
      <Link href="/" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" /> Salle des marchés
      </Link>

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <ProfileChip profile={m.profile} />
            <Badge variant={m.stage === "paper" ? "success" : "outline"}>{STAGE_LABEL[m.stage]}</Badge>
            <span className="text-xs text-muted-foreground">
              {p.broker === "alpaca_paper" ? "Alpaca paper" : "simulateur"} · déployé le{" "}
              {m.promoted_at ? new Date(m.promoted_at).toLocaleDateString("fr-FR", { day: "numeric", month: "long" }) : "—"}
              {m.last_decision_on ? ` · dernière décision ${m.last_decision_on}` : ""}
            </span>
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">{m.name}</h1>
          <p className="max-w-3xl text-sm text-muted-foreground">{m.description}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link href={`/lab/${m.id}`}><Button variant="outline"><FlaskConical className="size-3.5" /> Paramètres & backtest</Button></Link>
          {m.stage === "paper" && (
            <>
              <Button variant="outline" disabled={!!busy}
                onClick={() => act("run", `/api/lab/models/${m.id}/run-now`, "Exécuter la décision maintenant, hors de l'horaire prévu ?")}>
                {busy === "run" ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />} Décider maintenant
              </Button>
              <Button variant="destructive" disabled={!!busy}
                onClick={() => act("retire", `/api/lab/models/${m.id}/retire`, "Retirer ce modèle ? Toutes ses positions seront vendues.")}>
                <Power className="size-3.5" /> Retirer
              </Button>
            </>
          )}
        </div>
      </header>

      {error && <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-700">{error}</p>}

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Valeur" hint={`départ ${usd(m.budget, { signed: false })}`}>{usd(p.equity, { signed: false })}</Stat>
        <Stat label="Gain / perte" hint={pct(p.pnl_pct)}><Money value={p.pnl_usd} /></Stat>
        <Stat label="Aujourd'hui"><Money value={p.today_usd} /></Stat>
        <Stat label="Pire creux"><Money value={p.max_drawdown_usd} signed={false} /></Stat>
        <Stat label="Cash disponible" hint={`${usd(p.invested, { signed: false })} investis`}>{usd(p.cash, { signed: false })}</Stat>
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Réel contre backtest, sur la même période</CardTitle>
          <p className="text-xs text-muted-foreground">
            {d.replay.available
              ? `Le backtest rejoué utilise exactement le même code et les mêmes paramètres : l'écart vient du marché et de l'exécution, pas du modèle. Backtest : ${usd(d.replay.pnl_usd)}, réel : ${usd(p.pnl_usd)}.`
              : `Comparaison disponible après deux séances complètes (${d.replay.reason}).`}
          </p>
        </CardHeader>
        <CardContent>
          {chart && chart.dates.length > 0 ? (
            <EquityChart dates={chart.dates} series={chart.series} height={260} />
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">La courbe se construit à chaque passage de la boucle.</p>
          )}
        </CardContent>
      </Card>

      {d.feedback?.available && <FeedbackCard f={d.feedback} modelId={m.id} />}

      <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
        <div className="space-y-6">
          <Card>
            <CardHeader><CardTitle>Positions</CardTitle></CardHeader>
            <CardContent>
              {now.holdings?.length ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Titre</TableHead>
                      <TableHead className="text-right">Qté</TableHead>
                      <TableHead className="text-right">Achat</TableHead>
                      <TableHead className="text-right">Cours</TableHead>
                      <TableHead className="text-right">Valeur</TableHead>
                      <TableHead className="text-right">Résultat</TableHead>
                      <TableHead className="text-right" title="Recul depuis le plus haut atteint depuis l'achat — ce que surveille le stop suiveur">Depuis le plus haut</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {now.holdings.map((h) => (
                      <TableRow key={h.symbol}>
                        <TableCell><span className="font-medium">{h.symbol}</span> {h.label !== h.symbol && <span className="text-muted-foreground">{h.label}</span>}</TableCell>
                        <TableCell className="text-right tabular-nums">{qty(h.qty)}</TableCell>
                        <TableCell className="text-right tabular-nums">{usd(h.entry_price, { signed: false, cents: true })}</TableCell>
                        <TableCell className="text-right tabular-nums">{usd(h.price, { signed: false, cents: true })}</TableCell>
                        <TableCell className="text-right"><Money value={h.value} signed={false} neutral /></TableCell>
                        <TableCell className="text-right"><Money value={h.pnl_usd} /> <span className="text-xs text-muted-foreground">{pct(h.pnl_pct)}</span></TableCell>
                        <TableCell className={cn("text-right tabular-nums", h.from_peak_pct < -5 ? "text-amber-700" : "text-muted-foreground")}>{pct(h.from_peak_pct)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <p className="text-sm text-muted-foreground">
                  Aucune position{p.open_orders ? ` — ${p.open_orders} ordre(s) en attente d'exécution` : ""}.
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Si le modèle décidait maintenant</CardTitle>
              <p className="text-xs text-muted-foreground">
                Calculé à l&apos;instant, sans rien exécuter. La vraie décision a lieu chaque jour peu avant la clôture.
              </p>
            </CardHeader>
            <CardContent>
              {now.error ? (
                <p className="text-sm text-red-700">{now.error}</p>
              ) : p.open_orders > 0 ? (
                <p className="text-sm text-muted-foreground">
                  {p.open_orders} ordre{p.open_orders > 1 ? "s" : ""} en attente d&apos;exécution
 : aucune nouvelle décision tant qu&apos;ils ne sont pas exécutés — sinon le modèle
                  achèterait deux fois ce qu&apos;il a déjà commandé.
                </p>
              ) : pending.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  Rien à faire : le portefeuille est conforme à ce que veut le modèle.
                  {m.cadence === "monthly" && ` Prochain rééquilibrage à la première séance de ${nextMonth()} ; d'ici là, les stops sont vérifiés chaque jour.`}
                </p>
              ) : (
                <ul className="space-y-1 text-sm">
                  {pending.map((o, i) => (
                    <li key={i} className="flex justify-between gap-3">
                      <span>
                        <span className={o.side === "buy" ? "text-blue-700" : "text-amber-700"}>{o.side === "buy" ? "Acheter" : "Vendre"}</span>{" "}
                        <b>{o.symbol}</b> <span className="text-muted-foreground">{o.note || o.reason}</span>
                      </span>
                      <Money value={o.notional} signed={false} neutral />
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Ordres</CardTitle></CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Quand</TableHead>
                    <TableHead>Sens</TableHead>
                    <TableHead>Titre</TableHead>
                    <TableHead className="text-right">Qté</TableHead>
                    <TableHead className="text-right">Prix exécuté</TableHead>
                    <TableHead>Statut</TableHead>
                    <TableHead className="text-right">Réalisé</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {d.orders.map((o) => (
                    <TableRow key={o.id} title={`${o.reason}${o.broker_order_id ? ` · ${o.broker_order_id}` : ""}`}>
                      <TableCell className="tabular-nums text-muted-foreground">
                        {new Date(o.created_at).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}
                      </TableCell>
                      <TableCell className={o.side === "buy" ? "text-blue-700" : "text-amber-700"}>
                        {o.purpose === "safety_stop" ? "Stop de secours" : o.side === "buy" ? "Achat" : "Vente"}
                      </TableCell>
                      <TableCell className="font-medium">
                        {o.symbol}
                        {o.purpose === "safety_stop" && o.stop_price ? <span className="ml-1 text-xs font-normal text-muted-foreground">à {usd(o.stop_price, { signed: false, cents: true })}</span> : null}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{qty(o.qty)}</TableCell>
                      <TableCell className="text-right tabular-nums">{o.filled_avg_price ? usd(o.filled_avg_price, { signed: false, cents: true }) : "—"}</TableCell>
                      <TableCell className={STATUS[o.status]?.cls}>{STATUS[o.status]?.label ?? o.status}</TableCell>
                      <TableCell className="text-right">{o.realized_pnl == null ? "" : <Money value={o.realized_pnl} />}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>

        <Card className="self-start">
          <CardHeader><CardTitle>Journal</CardTitle></CardHeader>
          <CardContent className="max-h-[80vh] overflow-y-auto">
            <Journal events={d.events} showModel={false} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
