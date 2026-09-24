"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Check, Lock, PlugZap, RefreshCw, Scale } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { api, ApiError, fmtMoney } from "@/lib/api";

type FieldType = "float" | "int" | "bool" | "str" | "enum" | "secret";

type SettingField = {
  key: string;
  label: string;
  type: FieldType;
  help: string;
  unit: string;
  choices: string[];
  minimum: number | null;
  maximum: number | null;
  available: boolean;
  unavailable_reason: string;
  overridden: boolean;
  source: "database" | "environment" | "unset";
  value: string | number | boolean | null;
  configured?: boolean;
  masked?: string;
};

type SettingGroup = { key: string; label: string; description: string; fields: SettingField[] };

type MonitorStatus = {
  running: boolean;
  enabled: boolean;
  interval_seconds: number;
  passes: number;
  last_run_at: string | null;
  last_duration_ms: number | null;
  last_error: string | null;
  last_result: { decided?: number; fills?: number; snapshots?: number; errors?: number };
};

type Broker = { name: string; label: string; available: boolean; needs_credentials: boolean; description: string };

type ConnectionTest = {
  ok: boolean;
  stage: string;
  detail: string;
  account?: { account_number: string; status: string; currency: string; cash: number; equity: number; buying_power: number };
  clock?: { is_open?: boolean; next_open?: string | null; next_close?: string | null; error?: string };
};

type Reconciliation = {
  ok: boolean;
  portfolios: number;
  netted?: boolean;
  detail: string;
  differences: { symbol: string; ours: number; venue: number; delta: number }[];
};

type SettingsPayload = {
  groups: SettingGroup[];
  brokers: Broker[];
  monitor: MonitorStatus;
  kill_switch: { engaged: boolean; reason: string };
  trading_mode: string;
};

type Draft = Record<string, string | number | boolean>;

export default function SettingsPage() {
  const [data, setData] = useState<SettingsPayload | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api<SettingsPayload>("/api/settings"));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Impossible de charger les réglages.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // The monitor panel is a live view of a background loop, so it needs to keep
  // refreshing on its own — but only while nothing is being edited, so a poll
  // can never overwrite what someone is in the middle of typing.
  useEffect(() => {
    if (Object.keys(draft).length > 0) return;
    const t = setInterval(() => void load(), 10_000);
    return () => clearInterval(t);
  }, [draft, load]);

  const dirty = Object.keys(draft).length > 0;

  function edit(key: string, value: string | number | boolean) {
    setSaved(false);
    setDraft((d) => ({ ...d, [key]: value }));
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const payload = await api<SettingsPayload>("/api/settings", {
        method: "PUT",
        body: JSON.stringify({ values: draft }),
      });
      setData(payload);
      setDraft({});
      setSaved(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Échec de l'enregistrement.");
    } finally {
      setBusy(false);
    }
  }

  async function runMonitorOnce() {
    setBusy(true);
    setError(null);
    try {
      await api("/api/settings/monitor/run-once", { method: "POST" });
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Le passage du monitor a échoué.");
    } finally {
      setBusy(false);
    }
  }

  async function toggleKillSwitch(engaged: boolean) {
    setBusy(true);
    try {
      await api("/api/settings/kill-switch", {
        method: "POST",
        body: JSON.stringify({ engaged, reason: engaged ? "Arrêt manuel depuis l'administration" : "" }),
      });
      await load();
    } finally {
      setBusy(false);
    }
  }

  if (!data) {
    return (
      <div className="p-6 space-y-2">
        <h1 className="text-2xl font-semibold">Administration</h1>
        <p className="text-sm text-muted-foreground">{error ?? "Chargement…"}</p>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6 max-w-4xl">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">Administration</h1>
        <p className="text-sm text-muted-foreground">
          Réglages appliqués à chaud : le monitor relit ces valeurs à chaque passage, aucun
          redémarrage n&apos;est nécessaire. Le mode de trading est figé sur{" "}
          <strong>{data.trading_mode}</strong> et n&apos;est exposé par aucune API.
        </p>
      </header>

      <KillSwitchCard state={data.kill_switch} busy={busy} onToggle={toggleKillSwitch} />
      <MonitorCard status={data.monitor} busy={busy} onRunOnce={runMonitorOnce} />

      {data.groups.map((group) => (
        <Card key={group.key}>
          <CardHeader>
            <CardTitle>{group.label}</CardTitle>
            <p className="text-sm text-muted-foreground">{group.description}</p>
          </CardHeader>
          <CardContent className="space-y-5">
            {group.fields.map((field) => (
              <FieldRow
                key={field.key}
                field={field}
                draft={draft[field.key]}
                onChange={(v) => edit(field.key, v)}
              />
            ))}
            {group.key === "execution" && <BrokerList brokers={data.brokers} />}
            {group.key === "connections" && <AlpacaConnection dirty={dirty} />}
            {group.key === "notify" && <NotifyHelpers onChange={load} />}
          </CardContent>
        </Card>
      ))}

      {error && (
        <p className="text-sm text-red-600 flex items-start gap-2">
          <AlertTriangle className="size-4 mt-0.5 shrink-0" />
          {error}
        </p>
      )}

      <div className="sticky bottom-0 flex items-center gap-3 border-t bg-background/90 py-3 backdrop-blur">
        <Button onClick={save} disabled={!dirty || busy}>
          {busy ? "Enregistrement…" : `Enregistrer${dirty ? ` (${Object.keys(draft).length})` : ""}`}
        </Button>
        {dirty && (
          <Button variant="outline" onClick={() => setDraft({})} disabled={busy}>
            Annuler
          </Button>
        )}
        {saved && !dirty && (
          <span className="text-sm text-emerald-600 flex items-center gap-1">
            <Check className="size-4" /> Enregistré
          </span>
        )}
      </div>
    </div>
  );
}

function FieldRow({
  field,
  draft,
  onChange,
}: {
  field: SettingField;
  draft: string | number | boolean | undefined;
  onChange: (v: string | number | boolean) => void;
}) {
  const disabled = !field.available;
  const edited = draft !== undefined;

  return (
    <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_260px] sm:items-start">
      <div className="space-y-1">
        <div className="flex items-center gap-2 flex-wrap">
          <label className="text-sm font-medium">{field.label}</label>
          {field.type === "secret" && <Lock className="size-3 text-muted-foreground" />}
          {edited && <Badge variant="outline">modifié</Badge>}
          {!edited && field.overridden && <Badge variant="secondary">personnalisé</Badge>}
          {!field.available && <Badge variant="outline">indisponible</Badge>}
        </div>
        <p className="text-xs text-muted-foreground">{field.help}</p>
        {!field.available && field.unavailable_reason && (
          <p className="text-xs text-amber-600">{field.unavailable_reason}</p>
        )}
      </div>
      <div className="space-y-1">
        <FieldControl field={field} draft={draft} disabled={disabled} onChange={onChange} />
        {field.type === "secret" && (
          <p className="text-xs text-muted-foreground">
            {field.configured
              ? `Configuré (${field.masked}) — source : ${field.source === "database" ? "base" : "environnement"}.`
              : "Non configuré."}{" "}
            La valeur enregistrée n&apos;est jamais renvoyée au navigateur.
          </p>
        )}
      </div>
    </div>
  );
}

function FieldControl({
  field,
  draft,
  disabled,
  onChange,
}: {
  field: SettingField;
  draft: string | number | boolean | undefined;
  disabled: boolean;
  onChange: (v: string | number | boolean) => void;
}) {
  const selectClass = "h-9 w-full rounded-lg border border-input bg-transparent px-2 text-sm disabled:opacity-50";

  if (field.type === "bool") {
    const value = draft !== undefined ? Boolean(draft) : Boolean(field.value);
    return (
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="size-4"
          checked={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
        />
        {value ? "Activé" : "Désactivé"}
      </label>
    );
  }

  if (field.type === "enum") {
    const value = draft !== undefined ? String(draft) : String(field.value ?? "");
    return (
      <select className={selectClass} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        {field.choices.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    );
  }

  if (field.type === "secret") {
    return (
      <div className="flex gap-2">
        <Input
          type="password"
          autoComplete="new-password"
          placeholder={field.configured ? field.masked : "Coller la clé"}
          value={draft !== undefined ? String(draft) : ""}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
        {field.overridden && (
          <Button variant="outline" disabled={disabled} onClick={() => onChange("")}>
            Effacer
          </Button>
        )}
      </div>
    );
  }

  const numeric = field.type === "float" || field.type === "int";
  return (
    <div className="flex items-center gap-2">
      <Input
        type={numeric ? "number" : "text"}
        step={field.type === "int" ? 1 : "any"}
        min={field.minimum ?? undefined}
        max={field.maximum ?? undefined}
        value={draft !== undefined ? String(draft) : String(field.value ?? "")}
        disabled={disabled}
        onChange={(e) => onChange(numeric ? Number(e.target.value) : e.target.value)}
      />
      {field.unit && <span className="text-sm text-muted-foreground shrink-0">{field.unit}</span>}
    </div>
  );
}

function NotifyHelpers({ onChange }: { onChange: () => void }) {
  const [topic, setTopic] = useState<{ topic: string; subscribe_url: string } | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function generate() {
    setBusy(true);
    try {
      setTopic(await api<{ topic: string; subscribe_url: string }>("/api/settings/notify/topic", { method: "POST" }));
      onChange();
    } finally {
      setBusy(false);
    }
  }
  async function test() {
    setBusy(true);
    try {
      const r = await api<{ delivered: string[] }>("/api/settings/notify/test", { method: "POST" });
      setMsg({ ok: true, text: `Envoyé via ${r.delivered.join(" et ")}.` });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Échec de l'envoi." });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="rounded-lg border p-3 space-y-2">
      <p className="text-sm font-medium">Recevoir les notifications sur ton téléphone</p>
      <ol className="list-decimal space-y-0.5 pl-5 text-xs text-muted-foreground">
        <li>Installe l&apos;appli <b>ntfy</b> (iOS ou Android, gratuite, sans compte).</li>
        <li>Génère un sujet ci-dessous, puis abonne-toi à ce sujet dans l&apos;appli.</li>
        <li>Active les notifications, enregistre, et envoie un test.</li>
      </ol>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={generate} disabled={busy}>Générer un sujet</Button>
        <Button variant="outline" size="sm" onClick={test} disabled={busy}>Envoyer un test</Button>
      </div>
      {topic && (
        <p className="text-xs">
          Sujet : <code className="rounded bg-muted px-1">{topic.topic}</code> — à noter maintenant, il ne sera plus
          réaffiché. Lien d&apos;abonnement : <a className="underline" href={topic.subscribe_url} target="_blank" rel="noreferrer">{topic.subscribe_url}</a>
        </p>
      )}
      {msg && <p className={msg.ok ? "text-xs text-emerald-700" : "text-xs text-red-700"}>{msg.text}</p>}
    </div>
  );
}

function AlpacaConnection({ dirty }: { dirty: boolean }) {
  const [test, setTest] = useState<ConnectionTest | null>(null);
  const [rec, setRec] = useState<Reconciliation | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function run(what: "test" | "reconcile") {
    setBusy(what);
    try {
      if (what === "test") {
        setTest(await api<ConnectionTest>("/api/settings/connections/alpaca_paper/test", { method: "POST" }));
      } else {
        setRec(await api<Reconciliation>("/api/settings/connections/alpaca_paper/reconcile"));
      }
    } catch (e) {
      const detail = e instanceof ApiError ? e.message : "Appel impossible.";
      if (what === "test") setTest({ ok: false, stage: "request", detail });
      else setRec({ ok: false, portfolios: 0, detail, differences: [] });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="rounded-lg border p-3 space-y-3">
      <div>
        <p className="text-sm font-medium">Alpaca paper</p>
        <p className="text-xs text-muted-foreground">
          Vérifie les identifiants enregistrés contre le venue, et compare le livre local aux
          positions réellement détenues chez Alpaca. Aucun identifiant n&apos;est renvoyé par ces
          appels.
        </p>
      </div>
      {dirty && (
        <p className="text-xs text-amber-600">
          Des modifications ne sont pas enregistrées — le test utilise les valeurs déjà en base.
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" disabled={busy !== null} onClick={() => run("test")}>
          <PlugZap className="size-4" /> {busy === "test" ? "Test en cours…" : "Tester la connexion"}
        </Button>
        <Button variant="outline" disabled={busy !== null} onClick={() => run("reconcile")}>
          <Scale className="size-4" /> {busy === "reconcile" ? "Réconciliation…" : "Réconcilier les positions"}
        </Button>
      </div>

      {test && (
        <div className="space-y-1 text-sm">
          <p className={test.ok ? "text-emerald-600" : "text-red-600"}>
            {test.ok ? "✓" : "✕"} {test.detail}
          </p>
          {test.account && (
            <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-4">
              <Stat label="Compte" value={test.account.account_number || "—"} />
              <Stat label="Statut" value={test.account.status.split(".").pop() ?? "—"} />
              <Stat label="Équité" value={fmtMoney(test.account.equity)} />
              <Stat label="Pouvoir d'achat" value={fmtMoney(test.account.buying_power)} />
            </dl>
          )}
          {test.clock && test.clock.error == null && (
            <p className="text-xs text-muted-foreground">
              Marché {test.clock.is_open ? "ouvert" : "fermé"}
              {!test.clock.is_open && test.clock.next_open
                ? ` — réouverture ${new Date(test.clock.next_open).toLocaleString("fr-FR")}`
                : ""}
            </p>
          )}
        </div>
      )}

      {rec && (
        <div className="space-y-1 text-sm">
          <p className={rec.ok ? "text-emerald-600" : "text-amber-600"}>
            {rec.ok ? "✓" : "!"} {rec.detail}
          </p>
          {rec.netted && (
            <p className="text-xs text-amber-600">
              Plusieurs variantes partagent ce compte : le venue nette leurs positions, donc
              l&apos;attribution par variante n&apos;est plus réelle.
            </p>
          )}
          {rec.differences.length > 0 && (
            <ul className="text-xs font-mono space-y-0.5">
              {rec.differences.map((d) => (
                <li key={d.symbol}>
                  {d.symbol}: livre {d.ours} · venue {d.venue} ({d.delta > 0 ? "+" : ""}
                  {d.delta})
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function BrokerList({ brokers }: { brokers: Broker[] }) {
  return (
    <div className="rounded-lg border divide-y">
      {brokers.map((b) => (
        <div key={b.name} className="p-3 space-y-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">{b.label}</span>
            <code className="text-xs text-muted-foreground">{b.name}</code>
            <Badge variant={b.available ? "secondary" : "outline"}>
              {b.available ? "disponible" : "non implémenté"}
            </Badge>
            {b.needs_credentials && <Badge variant="outline">identifiants requis</Badge>}
          </div>
          <p className="text-xs text-muted-foreground">{b.description}</p>
        </div>
      ))}
    </div>
  );
}

function MonitorCard({
  status,
  busy,
  onRunOnce,
}: {
  status: MonitorStatus;
  busy: boolean;
  onRunOnce: () => void;
}) {
  const r = status.last_result ?? {};
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Monitor
          <Badge variant={status.running && status.enabled ? "secondary" : "outline"}>
            {status.running ? (status.enabled ? "en marche" : "en pause") : "arrêté"}
          </Badge>
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          Toutes les {status.interval_seconds}s : sonde les ordres en attente, fait décider les modèles
          peu avant la clôture, enregistre l&apos;équité et réconcilie avec Alpaca.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
          <Stat label="Passages" value={String(status.passes)} />
          <Stat
            label="Dernier"
            value={status.last_run_at ? new Date(status.last_run_at).toLocaleTimeString("fr-FR") : "—"}
          />
          <Stat label="Durée" value={status.last_duration_ms != null ? `${status.last_duration_ms} ms` : "—"} />
          <Stat label="Décisions" value={r.decided != null ? String(r.decided) : "—"} />
          <Stat label="Exécutions reçues" value={r.fills != null ? String(r.fills) : "—"} />
          <Stat label="Relevés d'équité" value={r.snapshots != null ? String(r.snapshots) : "—"} />
        </dl>
        {status.last_error && (
          <p className="text-sm text-red-600 flex items-start gap-2">
            <AlertTriangle className="size-4 mt-0.5 shrink-0" />
            {status.last_error}
          </p>
        )}
        <Button variant="outline" onClick={onRunOnce} disabled={busy}>
          <RefreshCw className="size-4" /> Lancer un passage maintenant
        </Button>
      </CardContent>
    </Card>
  );
}

function KillSwitchCard({
  state,
  busy,
  onToggle,
}: {
  state: { engaged: boolean; reason: string };
  busy: boolean;
  onToggle: (engaged: boolean) => void;
}) {
  return (
    <Card className={state.engaged ? "border-red-500/50" : undefined}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Kill-switch global
          <Badge variant={state.engaged ? "destructive" : "secondary"}>
            {state.engaged ? "engagé" : "relâché"}
          </Badge>
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          Bloque tout nouvel ordre, quel que soit le modèle ou le broker. Les positions
          existantes ne sont pas liquidées.
        </p>
      </CardHeader>
      <CardContent className="space-y-2">
        {state.engaged && state.reason && (
          <p className="text-sm text-muted-foreground">Raison : {state.reason}</p>
        )}
        <Button
          variant={state.engaged ? "default" : "destructive"}
          disabled={busy}
          onClick={() => onToggle(!state.engaged)}
        >
          {state.engaged ? "Relâcher le kill-switch" : "Engager le kill-switch"}
        </Button>
      </CardContent>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  );
}
