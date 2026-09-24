"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Plus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import Money from "@/components/money";
import Sparkline from "@/components/sparkline";
import { api, ApiError } from "@/lib/api";
import { PROFILE_META, PROFILE_ORDER, STAGE_LABEL, pct, type Model, type ProfileSchema } from "@/lib/lab";

export default function LabPage() {
  const router = useRouter();
  const [profiles, setProfiles] = useState<ProfileSchema[]>([]);
  const [models, setModels] = useState<Model[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      const [p, m] = await Promise.all([api<ProfileSchema[]>("/api/lab/profiles"), api<Model[]>("/api/lab/models")]);
      setProfiles(p);
      setModels(m);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "API injoignable.");
    }
  }
  useEffect(() => void load(), []);

  async function seed() {
    await api("/api/lab/models/seed", { method: "POST" });
    await load();
  }

  async function create(profile: ProfileSchema) {
    const m = await api<Model>("/api/lab/models", {
      method: "POST",
      body: JSON.stringify({ profile: profile.key, name: `${profile.label} — nouveau`, params: {} }),
    });
    router.push(`/lab/${m.id}`);
  }

  if (!models) return <p className="text-sm text-muted-foreground">{error ?? "Chargement…"}</p>;

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Laboratoire</h1>
          <p className="max-w-3xl text-sm text-muted-foreground">
            Trois profils, trois horizons. Chaque modèle est un jeu de paramètres sur un profil : réglez-le, backtestez-le
            sur 10 ans de données réelles, et déployez-le en paper s&apos;il le mérite.
          </p>
        </div>
        {models.length === 0 && <Button onClick={seed}>Créer les 9 modèles de départ</Button>}
      </header>

      {PROFILE_ORDER.map((key) => {
        const profile = profiles.find((p) => p.key === key);
        if (!profile) return null;
        const meta = PROFILE_META[key];
        const list = models.filter((m) => m.profile === key);
        return (
          <section key={key} className="space-y-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="flex items-start gap-3">
                <span className={`grid size-10 place-items-center rounded-xl ${meta.soft} ${meta.text}`}>
                  <meta.Icon className="size-5" />
                </span>
                <div>
                  <h2 className="text-lg font-semibold">
                    {profile.label} <span className="text-sm font-normal text-muted-foreground">· {profile.nickname}</span>
                  </h2>
                  <p className="text-xs text-muted-foreground">
                    Décide {profile.cadence === "daily" ? "chaque jour" : "chaque mois"} · tient {profile.horizon}
                  </p>
                  <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{profile.description}</p>
                </div>
              </div>
              <Button variant="outline" size="sm" onClick={() => create(profile)}>
                <Plus className="size-3.5" /> Nouveau modèle
              </Button>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {list.map((m) => (
                <ModelCard key={m.id} m={m} />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function ModelCard({ m }: { m: Model }) {
  const b = m.backtest;
  const meta = PROFILE_META[m.profile];
  return (
    <Link href={m.stage === "paper" ? `/paper/${m.id}` : `/lab/${m.id}`}
      className={`block rounded-xl bg-card p-4 ring-1 ring-foreground/10 transition hover:ring-2 ${meta.ring}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-medium">{m.name}</div>
          <p className="line-clamp-2 text-xs text-muted-foreground">{m.description}</p>
        </div>
        <Badge variant={m.stage === "paper" ? "success" : "outline"}>{STAGE_LABEL[m.stage]}</Badge>
      </div>
      {b ? (
        <div className="mt-3 flex items-end justify-between gap-2">
          <div className="text-sm">
            <Money value={b.pnl_usd} className="text-lg font-semibold" />
            <div className="text-xs text-muted-foreground">
              {b.start.slice(0, 4)}→{b.end.slice(0, 4)} · creux <Money value={b.max_drawdown_usd} signed={false} /> · {pct(b.cagr_pct)}/an
            </div>
            <div className="text-xs">
              vs placebo <Money value={b.vs_placebo_usd} />
            </div>
          </div>
          <Sparkline values={b.spark} baseline={b.budget} width={100} height={36} color={meta.hex} />
        </div>
      ) : (
        <p className="mt-3 text-xs text-muted-foreground">Pas encore backtesté.</p>
      )}
    </Link>
  );
}
