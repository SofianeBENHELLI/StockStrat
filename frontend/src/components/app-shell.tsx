"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Compass, Dices, History, LayoutDashboard, type LucideIcon, Settings, Sigma } from "lucide-react";

const NAV: { section: string; items: { href: string; label: string; Icon: LucideIcon }[] }[] = [
  {
    section: "Tournoi",
    items: [
      { href: "/", label: "Tableau de bord", Icon: LayoutDashboard },
      { href: "/backtest", label: "Backtest", Icon: History },
    ],
  },
  {
    section: "Moteurs",
    items: [
      { href: "/strategies/casino", label: "Le Flambeur", Icon: Dices },
      { href: "/strategies/ml", label: "Le Matheux", Icon: Sigma },
      { href: "/strategies/economist", label: "Le Stratège", Icon: Compass },
    ],
  },
  {
    section: "Système",
    items: [{ href: "/settings", label: "Administration", Icon: Settings }],
  },
];

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="flex min-h-screen">
      <aside className="w-56 shrink-0 border-r bg-zinc-950 text-zinc-100 flex flex-col">
        <div className="px-4 py-5 border-b border-zinc-800">
          <div className="font-bold tracking-tight">Strategy Tournament</div>
          <div className="text-xs text-zinc-400">Paper trading · Simulation</div>
        </div>
        <nav className="flex-1 py-3">
          {NAV.map((group) => (
            <div key={group.section} className="mb-3">
              <div className="px-4 pb-1 text-[10px] font-semibold uppercase tracking-widest text-zinc-500">
                {group.section}
              </div>
              {group.items.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`flex items-center gap-2 px-4 py-2 text-sm hover:bg-zinc-800 ${
                    pathname === item.href
                      ? "bg-zinc-800 font-medium border-l-2 border-amber-400"
                      : "text-zinc-300"
                  }`}
                >
                  <item.Icon className="h-4 w-4 shrink-0" />
                  {item.label}
                </Link>
              ))}
            </div>
          ))}
        </nav>
        <div className="border-t border-zinc-800 px-4 py-3 text-[11px] text-zinc-500">
          Simulation — aucun ordre réel.
        </div>
      </aside>
      <div className="flex-1 flex flex-col min-w-0 bg-zinc-50 dark:bg-zinc-900">
        <div className="bg-amber-500/15 text-amber-700 dark:text-amber-400 text-center text-xs font-medium py-1.5 border-b border-amber-500/30">
          Simulation — aucun conseil en investissement. Paper trading uniquement, aucun ordre réel.
        </div>
        <main className="flex-1 mx-auto w-full max-w-6xl px-4 py-6">{children}</main>
      </div>
    </div>
  );
}
