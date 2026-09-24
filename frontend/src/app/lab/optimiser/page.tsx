"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, Loader2, Play, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import Money from "@/components/money";
import Scatter from "@/components/scatter";
import Stat from "@/components/stat";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { PROFILE_META, PROFILE_ORDER, usd, type Model, type OptimizeJob, type ProfileKey, type ProfileSchema } from "@/lib/lab";

export default function Optimiser() {
  const router = useRouter();
  const [profile, setProfile] = useState<ProfileKey>("stratege");
  const [schemas, setSchemas] = useState<ProfileSchema[]>([]);
  const [job, setJob] = useState<OptimizeJob | null>(null);
  const [start, setStart] = useState("2016-01-04");
  const [split, setSplit] = useState("2022-01-03");
  const [grid, setGrid] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);

  useEffect(() => {
    api<ProfileSchema[]>("/api/lab/profiles").then(setSchemas).catch(() => setError("API injoignable."));
  }, []);

  const loadLatest = useCallback(async (p: ProfileKey) => {
    setSelected(null);
    setJob(await api<OptimizeJob | null>(`/api/lab/optimize/latest/${p}`));
    setGrid((await api<{ combinations: number }>(`/api/lab/optimize/grid/${p}`)).combinations);
  }, []);
  useEffect(() => void loadLatest(profile), [profile, loadLatest]);

  useEffect(() => {
    if (!job || job.status !== "running") return;
    const t = setInterval(async () => setJob(await api<OptimizeJob>(`/api/lab/optimize/${job.id}`)), 1000);
    return () => clearInterval(t);
  }, [job]);

  async function run() {
    setError(null);
    try {
      setJob(await api<OptimizeJob>("/api/lab/optimize", {
        method: "POST", body: JSON.stringify({ profile, start, split }),
      }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  const schema = schemas.find((s) => s.key === profile);
  const label = (k: string) => schema?.params.find((p) => p.key === k)?.label ?? k;
  const fmtVal = (k: string, v: unknown) => {
    const spec = schema?.params.find((p) => p.key === k);
    if (spec?.type === "choice") return spec.choices.find((c) => c.value === v)?.label.split(" ")[0] ?? String(v);
    if (typeof v === "boolean") return v ? "oui" : "non";
    return `${v}${spec?.unit ? ` ${spec.unit}` : ""}`;
  };

  const ranked = useMemo(
    () => (job?.results ?? []).map((r, id) => ({ ...r, id })).sort((a, b) => b.in_sample_pnl_usd - a.in_sample_pnl_usd),
    [job]
  );
  const v = job?.verdict ?? {};
  const color = PROFILE_META[profile].hex;
  const bestId = ranked[0]?.id;

  async function create(params: Record<string, unknown>) {
    const name = `${schema?.label.replace("Le ", "") ?? ""} optimisé — ${Object.entries(params).map(([k, x]) => fmtVal(k, x)).join(", ")}`.slice(0, 110);
    const m = await api<Model>("/api/lab/models", {
      method: "POST",
      body: JSON.stringify({ profile, name, params, description: `Issu de l'optimiseur (choix sur ${start.slice(0, 4)}–${split.slice(0, 4)}).` }),
    });
    router.push(`/lab/${m.id}`);
  }

  return (
    <div className="space-y-6">
      <Link href="/lab" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-3.5" /> Laboratoire
      </Link>
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Optimiseur</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Essaie toutes les combinaisons de paramètres d&apos;un profil, les classe sur une première période, puis regarde ce
          qu&apos;elles ont donné <b>ensuite</b>, sur une période qui n&apos;a servi à rien choisir. La question n&apos;est pas « quels
          sont les meilleurs réglages ? » mais « le meilleur du passé le reste-t-il ? ».
        </p>
      </header>

      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 py-1">
          <div className="flex gap-1">
            {PROFILE_ORDER.map((k) => {
              const M = PROFILE_META[k];
              return (
                <Button key={k} variant={profile === k ? "default" : "outline"} onClick={() => setProfile(k)} disabled={job?.status === "running"}>
                  <M.Icon className="size-3.5" /> {schemas.find((s) => s.key === k)?.label ?? k}
                </Button>
              );
            })}
          </div>
          <label className="text-xs text-muted-foreground">Choisir sur la période du
            <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} className="mt-1 w-40" />
          </label>
          <label className="text-xs text-muted-foreground">au / puis vérifier à partir du
            <Input type="date" value={split} onChange={(e) => setSplit(e.target.value)} className="mt-1 w-40" />
          </label>
          <Button onClick={run} disabled={job?.status === "running"}>
            {job?.status === "running" ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
            Tester {grid ?? "…"} combinaisons
          </Button>
        </CardContent>
      </Card>

      {error && <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-700">{error}</p>}
      {job?.status === "failed" && <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-700">Échec : {job.error}</p>}

      {job?.status === "running" && (
        <Card>
          <CardContent className="space-y-2 py-2">
            <div className="flex justify-between text-sm">
              <span>{job.done} / {job.total} backtests</span>
              <span className="text-muted-foreground">{job.elapsed_s.toFixed(0)} s</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-muted">
              <div className="h-full transition-all" style={{ width: `${(job.done / job.total) * 100}%`, background: color }} />
            </div>
          </CardContent>
        </Card>
      )}

      {job?.status === "done" && v.best_in_sample && (
        <>
          <div className={cn("rounded-xl p-4 ring-1", (v.rank_correlation ?? 0) >= 0.5 ? "bg-emerald-500/5 ring-emerald-500/30" : (v.rank_correlation ?? 0) >= 0.2 ? "bg-amber-500/5 ring-amber-500/30" : "bg-red-500/5 ring-red-500/30")}>
            <div className="text-sm font-medium">
              Corrélation de rang passé → futur : {(v.rank_correlation ?? 0).toFixed(2).replace(".", ",")}
            </div>
            <p className="mt-1 text-sm text-muted-foreground">{v.reading}</p>
          </div>

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label={`Le meilleur avant ${job.split.slice(0, 4)}`} hint={`sur le passé : ${usd(v.best_in_sample.in_sample_pnl_usd, { signed: true })}`}>
              <Money value={v.best_in_sample.out_of_sample_pnl_usd} />
            </Stat>
            <Stat label="…classé ensuite" hint={`sur ${v.combinations} combinaisons, depuis ${job.split.slice(0, 4)}`}>
              {v.best_in_sample.out_of_sample_rank}ᵉ
            </Stat>
            <Stat label="Combinaison médiane, ensuite" hint={`top 5 du passé : ${usd(v.top5_in_sample_mean_oos_usd, { signed: true })} en moyenne`}>
              <Money value={v.median_oos_usd} />
            </Stat>
            <Stat label="À battre ensuite" hint={`placebo · ${v.share_beating_placebo_oos_pct?.toString().replace(".", ",")} % des combinaisons y arrivent`}>
              <span className="text-violet-700"><Money value={v.placebo_oos_usd} neutral /></span>
              <span className="ml-2 text-sm font-normal text-muted-foreground">S&P {usd(v.spy_oos_usd, { signed: true })}</span>
            </Stat>
          </div>

          <div className="grid gap-6 xl:grid-cols-[1fr_1.1fr]">
            <Card>
              <CardHeader>
                <CardTitle>Chaque point est un jeu de paramètres</CardTitle>
                <p className="text-xs text-muted-foreground">
                  En abscisse ce qu&apos;il a gagné sur la période de choix, en ordonnée ce qu&apos;il a gagné ensuite. Un nuage qui
                  monte vers la droite : le passé prédit le futur. Un nuage informe : il ne le prédit pas. En violet, le
                  meilleur du passé.
                </p>
              </CardHeader>
              <CardContent>
                <Scatter
                  points={ranked.map((r) => ({ x: r.in_sample_pnl_usd, y: r.out_of_sample_pnl_usd, id: r.id, highlight: r.id === bestId || r.id === selected }))}
                  color={color}
                  xLabel={`Gain avant ${job.split.slice(0, 4)} (période de choix)`}
                  yLabel={`Gain depuis ${job.split.slice(0, 4)}`}
                  lines={[
                    { y: v.placebo_oos_usd ?? 0, label: "placebo", color: "#7c3aed" },
                    { y: v.spy_oos_usd ?? 0, label: "S&P 500", color: "#71717a" },
                  ]}
                  onPick={setSelected}
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader><CardTitle>Classées sur le passé</CardTitle></CardHeader>
              <CardContent className="max-h-[480px] overflow-y-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Paramètres</TableHead>
                      <TableHead className="text-right">Passé</TableHead>
                      <TableHead className="text-right">Ensuite</TableHead>
                      <TableHead className="text-right">vs placebo</TableHead>
                      <TableHead />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {ranked.slice(0, 30).map((r) => (
                      <TableRow key={r.id} className={cn(r.id === selected && "bg-violet-500/10")} onMouseEnter={() => setSelected(r.id)}>
                        <TableCell className="max-w-72">
                          <div className="flex flex-wrap gap-1">
                            {Object.entries(r.params).map(([k, x]) => (
                              <span key={k} className="rounded bg-muted px-1.5 py-0.5 text-[11px]" title={label(k)}>
                                {label(k).split(" ")[0]} <b>{fmtVal(k, x)}</b>
                              </span>
                            ))}
                          </div>
                        </TableCell>
                        <TableCell className="text-right"><Money value={r.in_sample_pnl_usd} /></TableCell>
                        <TableCell className="text-right"><Money value={r.out_of_sample_pnl_usd} className="font-semibold" /></TableCell>
                        <TableCell className="text-right"><Money value={r.out_of_sample_vs_placebo_usd} /></TableCell>
                        <TableCell className="text-right">
                          <Button size="sm" variant="ghost" onClick={() => create(r.params)} title="Créer un modèle avec ces paramètres">
                            <Plus className="size-3.5" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </div>
        </>
      )}

      {!job && (
        <p className="py-10 text-center text-sm text-muted-foreground">
          Aucune optimisation lancée pour ce profil. Elle prend de 15 s à une minute selon le profil.
        </p>
      )}
    </div>
  );
}
