import Link from "next/link";
import { AlertTriangle, ArrowDownRight, ArrowUpRight, CircleCheck, Info, Rocket, Scale } from "lucide-react";
import { cn } from "@/lib/utils";
import { ago, type LabEvent } from "@/lib/lab";

const KIND: Record<LabEvent["kind"], { Icon: typeof Info; tone: string }> = {
  decision: { Icon: Scale, tone: "text-zinc-600 bg-zinc-500/10" },
  order: { Icon: ArrowUpRight, tone: "text-blue-600 bg-blue-500/10" },
  fill: { Icon: CircleCheck, tone: "text-emerald-600 bg-emerald-500/10" },
  exit: { Icon: ArrowDownRight, tone: "text-amber-600 bg-amber-500/10" },
  error: { Icon: AlertTriangle, tone: "text-red-600 bg-red-500/10" },
  info: { Icon: Info, tone: "text-zinc-500 bg-zinc-500/10" },
  promote: { Icon: Rocket, tone: "text-violet-600 bg-violet-500/10" },
};

export default function Journal({ events, showModel = true, empty = "Rien pour l'instant." }: {
  events: LabEvent[];
  showModel?: boolean;
  empty?: string;
}) {
  if (!events.length) return <p className="text-sm text-muted-foreground py-6 text-center">{empty}</p>;
  return (
    <ol className="space-y-2.5">
      {events.map((e) => {
        const k = KIND[e.kind] ?? KIND.info;
        return (
          <li key={e.id} className="flex gap-2.5 text-sm">
            <span className={cn("mt-0.5 grid size-6 shrink-0 place-items-center rounded-md", k.tone)}>
              <k.Icon className="size-3.5" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="leading-snug">{e.message}</p>
              <p className="text-xs text-muted-foreground">
                {showModel && e.model && e.model_id ? (
                  <>
                    <Link href={`/paper/${e.model_id}`} className="hover:underline">{e.model}</Link> ·{" "}
                  </>
                ) : null}
                {ago(e.at)}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
