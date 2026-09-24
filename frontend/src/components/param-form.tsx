"use client";

import { cn } from "@/lib/utils";
import type { ParamSpec } from "@/lib/lab";

const GROUPS = ["Signal", "Portefeuille", "Risque"];

/** The parameter editor, generated from the profile schema the API serves.
 * A parameter that only matters for another mode (e.g. RSI settings when the
 * Flambeur is in breakout mode) is hidden rather than left to confuse. */
export default function ParamForm({
  specs,
  values,
  onChange,
  disabled,
}: {
  specs: ParamSpec[];
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  disabled?: boolean;
}) {
  const visible = specs.filter((p) => !p.visible_if || values[p.visible_if.key] === p.visible_if.value);
  return (
    <div className="space-y-5">
      {GROUPS.map((g) => {
        const items = visible.filter((p) => p.group === g);
        if (!items.length) return null;
        return (
          <fieldset key={g} className="space-y-3" disabled={disabled}>
            <legend className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">{g}</legend>
            {items.map((p) => (
              <Field key={p.key} spec={p} value={values[p.key]} onChange={(v) => onChange(p.key, v)} />
            ))}
          </fieldset>
        );
      })}
    </div>
  );
}

function Field({ spec, value, onChange }: { spec: ParamSpec; value: unknown; onChange: (v: unknown) => void }) {
  const changed = value !== spec.default;
  const input = "h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm tabular-nums disabled:opacity-60";
  return (
    <label className="block space-y-1" title={spec.help}>
      <span className="flex items-center gap-1.5 text-sm">
        {spec.label}
        {changed && <span className="size-1.5 rounded-full bg-violet-500" title={`Défaut : ${String(spec.default)}`} />}
      </span>
      {spec.type === "bool" ? (
        <span className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="size-4" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
          <span className="text-muted-foreground">{value ? "Oui" : "Non"}</span>
        </span>
      ) : spec.type === "choice" ? (
        <select className={input} value={String(value)} onChange={(e) => onChange(e.target.value)}>
          {spec.choices.map((c) => (
            <option key={c.value} value={c.value}>{c.label}</option>
          ))}
        </select>
      ) : (
        <span className="flex items-center gap-2">
          <input
            type="number"
            className={cn(input)}
            value={value === undefined || value === null ? "" : String(value)}
            min={spec.minimum ?? undefined}
            max={spec.maximum ?? undefined}
            step={spec.step ?? (spec.type === "int" ? 1 : "any")}
            onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
          />
          {spec.unit && <span className="shrink-0 text-xs text-muted-foreground">{spec.unit}</span>}
        </span>
      )}
      <span className="block text-xs leading-snug text-muted-foreground">{spec.help}</span>
    </label>
  );
}
