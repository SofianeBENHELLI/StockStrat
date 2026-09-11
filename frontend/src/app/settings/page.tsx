"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Check, Lock, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { api, ApiError } from "@/lib/api";

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
  last_result: { portfolios?: number; orders_resolved?: number; exits?: number; errors?: number };
};

type Broker = { name: string; label: string; available: boolean; description: string };

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
          Sonde les ordres en attente, applique les règles de sortie et enregistre l&apos;équité
          toutes les {status.interval_seconds}s.
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
          <Stat label="Portefeuilles" value={r.portfolios != null ? String(r.portfolios) : "—"} />
          <Stat label="Ordres résolus" value={r.orders_resolved != null ? String(r.orders_resolved) : "—"} />
          <Stat label="Sorties" value={r.exits != null ? String(r.exits) : "—"} />
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
          Bloque tout nouvel ordre, quelle que soit la variante ou le broker. Les positions
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
