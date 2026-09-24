"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { FlaskConical, LayoutDashboard, type LucideIcon, Settings, Trophy } from "lucide-react";

const NAV: { href: string; label: string; Icon: LucideIcon; match: (p: string) => boolean }[] = [
  { href: "/", label: "Salle des marchés", Icon: LayoutDashboard, match: (p) => p === "/" || p.startsWith("/paper") },
  { href: "/lab", label: "Laboratoire", Icon: FlaskConical, match: (p) => p.startsWith("/lab") },
  { href: "/backtest", label: "Classement", Icon: Trophy, match: (p) => p.startsWith("/backtest") },
  { href: "/settings", label: "Administration", Icon: Settings, match: (p) => p.startsWith("/settings") },
];

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 h-screen w-56 shrink-0 border-r bg-zinc-950 text-zinc-100 flex flex-col">
        <div className="px-4 py-5 border-b border-zinc-800">
          <div className="font-bold tracking-tight text-lg">
            Stock<span className="text-amber-400">Strat</span>
          </div>
          <div className="text-xs text-zinc-400">Labo de stratégies · paper trading</div>
        </div>
        <nav className="flex-1 py-3">
          {NAV.map((item) => {
            const active = item.match(pathname);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-2.5 px-4 py-2.5 text-sm hover:bg-zinc-800 ${
                  active ? "bg-zinc-800 font-medium border-l-2 border-amber-400" : "text-zinc-300 border-l-2 border-transparent"
                }`}
              >
                <item.Icon className="h-4 w-4 shrink-0" />
                {item.label}
              </Link>
            );
          })}
          <div className="mx-4 mt-6 space-y-2 text-[11px] leading-relaxed text-zinc-500">
            <p className="font-semibold uppercase tracking-widest text-zinc-600">Le cycle</p>
            <p>1. Régler un modèle au labo</p>
            <p>2. Le backtester sur 10 ans</p>
            <p>3. Déployer les meilleurs en paper</p>
            <p>4. Comparer réel et backtest</p>
          </div>
        </nav>
        <div className="border-t border-zinc-800 px-4 py-3 text-[11px] text-zinc-500">
          Paper trading Alpaca — aucun argent réel.
        </div>
      </aside>
      <div className="flex-1 flex flex-col min-w-0 bg-zinc-50">
        <main className="flex-1 mx-auto w-full max-w-7xl px-6 py-6">{children}</main>
      </div>
    </div>
  );
}
