"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, Copy, Loader2, Play, Rocket, Trash2, Wand2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import BacktestReport from "@/components/backtest-report";
import Money from "@/components/money";
import ParamForm from "@/components/param-form";
import ProfileChip from "@/components/profile-chip";
import { api, ApiError } from "@/lib/api";
import { PROFILE_META, STAGE_LABEL, qty, usd, type BacktestRun, type Model, type PlanPreview, type ProfileSchema } from "@/lib/lab";

const PERIODS = [
  { label: "Depuis 2016 (max)", start: "2016-01-04" },
  { label: "Depuis 2019", start: "2019-01-02" },
  { label: "3 dernières années", start: "" },
  { label: "Dernière année", start: "" },
];

function periodStart(label: string) {
  const p = PERIODS.find((x) => x.label === label)!;
  if (p.start) return p.start;
  const d = new Date();
  d.setFullYear(d.getFullYear() - (label.startsWith("3") ? 3 : 1));
  return d.toISOString().slice(0, 10);
}

export default function ModelPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [model, setModel] = useState<Model | null>(null);
  const [schema, setSchema] = useState<ProfileSchema | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [name, setName] = useState("");
  const [budget, setBudget] = useState(10_000);
  const [run, setRun] = useState<BacktestRun | null>(null);
  const [period, setPeriod] = useState(PERIODS[1].label);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<PlanPreview | null>(null);
  const [deployBudget, setDeployBudget] = useState(10_000);

  const load = useCallback(async () => {
    const m = await api<Model>(`/api/lab/models/${id}`);
    const profiles = await api<ProfileSchema[]>("/api/lab/profiles");
    setModel(m);
    setSchema(profiles.find((p) => p.key === m.profile) ?? null);
    setDraft(m.resolved_params);
    setName(m.name);
    setBudget(m.budget);
    setDeployBudget(m.budget);
    setRun(await api<BacktestRun | null>(`/api/lab/models/${id}/backtest`));
  }, [id]);

  useEffect(() => {
    load().catch((e) => setError(e instanceof ApiError ? e.message : "Modèle introuvable."));
  }, [load]);

  const overrides = useMemo(() => {
    if (!schema) return {};
    const out: Record<string, unknown> = {};
    for (const p of schema.params) if (draft[p.key] !== p.default) out[p.key] = draft[p.key];
    return out;
  }, [draft, schema]);

  const dirty = model != null && (JSON.stringify(overrides) !== JSON.stringify(model.params) || name !== model.name || budget !== model.budget);
  const frozen = model?.stage !== "lab";

  async function act<T>(label: string, fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(label);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    return act("save", async () => {
      const m = await api<Model>(`/api/lab/models/${id}`, {
        method: "PATCH",
        body: JSON.stringify(frozen ? { name } : { name, params: overrides, budget }),
      });
      setModel(m);
      return m;
    });
  }

  async function backtest() {
    if (dirty && !frozen) await save();
    await act("backtest", async () => {
      setRun(await api<BacktestRun>(`/api/lab/models/${id}/backtest`, {
        method: "POST",
        body: JSON.stringify({ start: periodStart(period), budget }),
      }));
    });
  }

  async function doPreview() {
    await act("preview", async () => setPreview(await api<PlanPreview>(`/api/lab/models/${id}/preview`)));
  }

  async function clone() {
    const m = await act("clone", () => api<Model>(`/api/lab/models/${id}/clone`, { method: "POST" }));
    if (m) router.push(`/lab/${m.id}`);
  }

  async function archive() {
    if (!confirm("Archiver ce modèle ? Il disparaîtra du labo (rien n'est supprimé en base).")) return;
    const ok = await act("archive", () => api(`/api/lab/models/${id}`, { method: "DELETE" }));
    if (ok) router.push("/lab");
  }

  async function deploy() {
    if (dirty) await save();
    const res = await act("deploy", () => api(`/api/lab/models/${id}/promote`, {
      method: "POST", body: JSON.stringify({ budget: deployBudget }),
    }));
    if (res) router.push(`/paper/${id}`);
  }

  if (!model || !schema) return <p className="text-sm text-muted-foreground">{error ?? "Chargement…"}</p>;
  const meta = PROFILE_META[model.profile];

  return (
    <div className="space-y-6">
      <Link href="/lab" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" /> Laboratoire
      </Link>

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 space-y-1">
          <div className="flex items-center gap-2">
            <ProfileChip profile={model.profile} />
            <Badge variant={model.stage === "paper" ? "success" : "outline"}>{STAGE_LABEL[model.stage]}</Badge>
            {model.parent_id && <span className="text-xs text-muted-foreground">clone du modèle #{model.parent_id}</span>}
          </div>
          <input value={name} onChange={(e) => setName(e.target.value)}
            className="w-full bg-transparent text-2xl font-semibold tracking-tight outline-none focus:underline" />
          <p className="max-w-3xl text-sm text-muted-foreground">{model.description || schema.description}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {model.stage === "paper" && (
            <Link href={`/paper/${model.id}`}><Button><Rocket className="size-3.5" /> Voir en paper</Button></Link>
          )}
          <Button variant="outline" onClick={clone} disabled={!!busy}><Copy className="size-3.5" /> Cloner</Button>
          {model.stage !== "paper" && (
            <Button variant="ghost" onClick={archive} disabled={!!busy}><Trash2 className="size-3.5" /> Archiver</Button>
          )}
        </div>
      </header>

      {error && <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-700">{error}</p>}

      <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
        <aside className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Paramètres</CardTitle>
              {frozen && (
                <p className="text-xs text-amber-700">
                  Figés : ce modèle trade en paper. Clonez-le pour essayer d&apos;autres réglages.
                </p>
              )}
            </CardHeader>
            <CardContent className="space-y-5">
              <label className="block space-y-1">
                <span className="text-sm">Budget</span>
                <span className="flex items-center gap-2">
                  <Input type="number" value={budget} min={100} step={500} disabled={frozen}
                    onChange={(e) => setBudget(Number(e.target.value))} />
                  <span className="text-xs text-muted-foreground">$</span>
                </span>
              </label>
              <ParamForm specs={schema.params} values={draft} disabled={frozen}
                onChange={(k, v) => setDraft((d) => ({ ...d, [k]: v }))} />
              {dirty && (
                <Button className="w-full" variant="secondary" onClick={save} disabled={!!busy}>
                  Enregistrer les modifications
                </Button>
              )}
            </CardContent>
          </Card>

          {model.stage === "lab" && (
            <Card className={`ring-2 ${meta.ring.replace("hover:", "")}`}>
              <CardHeader>
                <CardTitle>Déployer en paper</CardTitle>
                <p className="text-xs text-muted-foreground">
                  Ouvre une sous-comptabilité dédiée sur le compte Alpaca paper et passe immédiatement la première
                  décision. Marché fermé : les ordres attendent l&apos;ouverture.
                </p>
              </CardHeader>
              <CardContent className="space-y-3">
                <span className="flex items-center gap-2">
                  <Input type="number" value={deployBudget} min={100} step={500} onChange={(e) => setDeployBudget(Number(e.target.value))} />
                  <span className="text-xs text-muted-foreground">$</span>
                </span>
                <Button className="w-full" onClick={deploy} disabled={!!busy}>
                  {busy === "deploy" ? <Loader2 className="size-3.5 animate-spin" /> : <Rocket className="size-3.5" />}
                  Déployer avec {usd(deployBudget, { signed: false })}
                </Button>
              </CardContent>
            </Card>
          )}
        </aside>

        <div className="min-w-0 space-y-6">
          <Card>
            <CardContent className="flex flex-wrap items-center gap-3 py-1">
              <select value={period} onChange={(e) => setPeriod(e.target.value)}
                className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm">
                {PERIODS.map((p) => <option key={p.label}>{p.label}</option>)}
              </select>
              <Button onClick={backtest} disabled={!!busy}>
                {busy === "backtest" ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
                {busy === "backtest" ? "Backtest en cours…" : "Lancer le backtest"}
              </Button>
              <Button variant="outline" onClick={doPreview} disabled={!!busy}>
                {busy === "preview" ? <Loader2 className="size-3.5 animate-spin" /> : <Wand2 className="size-3.5" />}
                Que ferait-il aujourd&apos;hui ?
              </Button>
              {run && (
                <span className="text-xs text-muted-foreground">
                  Dernier backtest : {new Date(run.created_at).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" })}
                  {JSON.stringify(run.params) !== JSON.stringify(model.resolved_params) && " · paramètres modifiés depuis"}
                </span>
              )}
            </CardContent>
          </Card>

          {preview && <PreviewCard p={preview} />}

          {run ? (
            <BacktestReport run={run} profile={model.profile} />
          ) : (
            <p className="py-10 text-center text-sm text-muted-foreground">Aucun backtest pour ce modèle. Lancez-en un.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function PreviewCard({ p }: { p: PlanPreview }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Décision du jour, calculée sans rien exécuter</CardTitle>
        <p className="text-xs text-muted-foreground">
          Séance du {p.session} · {p.live ? "prix en direct" : "dernière clôture connue"} · sur un budget de {usd(p.equity, { signed: false })}
          {p.rebalance ? " · rééquilibrage complet" : ""}
        </p>
      </CardHeader>
      <CardContent>
        {p.orders.length === 0 ? (
          <p className="text-sm text-muted-foreground">Rien à faire aujourd&apos;hui : aucun signal ne justifie d&apos;agir.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Sens</TableHead>
                <TableHead>Titre</TableHead>
                <TableHead className="text-right">Qté</TableHead>
                <TableHead className="text-right">Montant</TableHead>
                <TableHead>Pourquoi</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {p.orders.map((o, i) => (
                <TableRow key={i}>
                  <TableCell className={o.side === "buy" ? "text-blue-700" : "text-amber-700"}>{o.side === "buy" ? "Achat" : "Vente"}</TableCell>
                  <TableCell><span className="font-medium">{o.symbol}</span> {o.label !== o.symbol && <span className="text-muted-foreground">{o.label}</span>}</TableCell>
                  <TableCell className="text-right tabular-nums">{qty(o.qty)}</TableCell>
                  <TableCell className="text-right"><Money value={o.notional} signed={false} neutral /></TableCell>
                  <TableCell className="text-muted-foreground">{o.note || o.reason}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
