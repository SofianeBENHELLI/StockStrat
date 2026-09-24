import { Compass, Dices, type LucideIcon, Sigma } from "lucide-react";

export type ProfileKey = "flambeur" | "matheux" | "stratege";

export type ParamSpec = {
  key: string;
  label: string;
  type: "int" | "float" | "bool" | "choice";
  default: number | boolean | string;
  group: string;
  help: string;
  minimum: number | null;
  maximum: number | null;
  step: number | null;
  unit: string;
  choices: { value: string; label: string }[];
  visible_if: { key: string; value: unknown } | null;
};

export type ProfileSchema = {
  key: ProfileKey;
  label: string;
  nickname: string;
  horizon: string;
  cadence: "daily" | "monthly";
  description: string;
  params: ParamSpec[];
  variants: { key: string; name: string; description: string; params: Record<string, unknown> }[];
};

export type BacktestBrief = {
  id: number;
  start: string;
  end: string;
  budget: number;
  created_at: string;
  pnl_usd: number;
  pnl_pct: number;
  max_drawdown_usd: number;
  max_drawdown_pct: number;
  cagr_pct: number;
  sharpe: number;
  vs_benchmark_usd: number;
  vs_placebo_usd: number;
  return_over_drawdown: number | null;
  exposure_pct: number;
  n_sells: number | null;
  hit_rate_pct: number | null;
  benchmark_pnl_usd: number;
  placebo_pnl_usd: number;
  spark: number[];
};

export type PaperStats = {
  equity: number;
  cash: number;
  invested: number;
  pnl_usd: number;
  pnl_pct: number;
  today_usd: number;
  max_drawdown_usd: number;
  positions: number;
  open_orders: number;
  protected?: number;
  broker: string;
  sparkline: number[];
};

export type Stage = "lab" | "paper" | "retired";

export type Model = {
  id: number;
  name: string;
  profile: ProfileKey;
  profile_label: string;
  variant_key: string;
  description: string;
  params: Record<string, unknown>;
  resolved_params: Record<string, unknown>;
  budget: number;
  stage: Stage;
  parent_id: number | null;
  cadence: "daily" | "monthly";
  promoted_at: string | null;
  last_decision_on: string | null;
  created_at: string;
  backtest?: BacktestBrief | null;
  paper?: PaperStats;
};

export type LabEvent = {
  id: number;
  model_id: number | null;
  model: string | null;
  linkable?: boolean;
  kind: "decision" | "order" | "fill" | "exit" | "error" | "info" | "promote";
  symbol: string;
  message: string;
  data: Record<string, unknown>;
  at: string;
};

export type Clock = { is_open: boolean; next_open?: string; next_close?: string; error?: string };

export type Reconciliation = {
  ok?: boolean;
  detail?: string;
  portfolios?: number;
  differences?: { symbol: string; ours: number; venue: number; delta: number }[];
  at?: string;
};

export type MonitorStatus = {
  running: boolean;
  enabled: boolean;
  interval_seconds: number;
  passes: number;
  last_run_at: string | null;
  last_error: string | null;
  last_result: Record<string, number>;
  clock: Clock;
  reconciliation: Reconciliation;
};

export type Overview = {
  models: Model[];
  totals: { allocated: number; equity: number; pnl_usd: number; today_usd: number; cap: number };
  clock: Clock;
  monitor: MonitorStatus;
  events: LabEvent[];
  now: string;
};

export type Trade = {
  day: string;
  symbol: string;
  label: string;
  side: "buy" | "sell";
  qty: number;
  price: number;
  reason: string;
  pnl: number | null;
  held_days: number | null;
};

export type Perf = {
  final_equity: number;
  pnl_usd: number;
  pnl_pct: number;
  cagr_pct: number;
  sharpe?: number;
  max_drawdown_usd: number;
  max_drawdown_pct: number;
};

export type Summary = Perf & {
  volatility_pct: number;
  sharpe: number;
  peak_day: string;
  trough_day: string;
  episode_pct?: number;
  recovered_day: string | null;
  exposure_pct: number;
  trades: {
    n_buys: number;
    n_sells: number;
    hit_rate_pct: number | null;
    avg_win_usd: number | null;
    avg_loss_usd: number | null;
    profit_factor: number | null;
    avg_hold_days: number | null;
    best_trade_usd: number | null;
    worst_trade_usd: number | null;
  };
  exit_reasons: Record<string, number>;
  benchmark: Perf & { symbol: string };
  vs_benchmark_usd: number;
  placebo: Perf;
  vs_placebo_usd: number;
  return_over_drawdown: number | null;
  yearly: { year: number; pnl_usd: number; pnl_pct: number; benchmark_pnl_pct: number }[];
  diagnostics: {
    months_scored?: number;
    mean_rank_correlation?: number;
    t_stat?: number;
    positive_months_pct?: number;
    last_train_size?: number;
    coefficients?: Record<string, number>;
  };
};

export type BacktestRun = {
  id: number;
  model_id: number;
  params: Record<string, unknown>;
  start: string;
  end: string;
  budget: number;
  summary: Summary;
  series: { dates: string[]; equity: number[]; benchmark: number[]; placebo: number[] };
  trades: Trade[];
  created_at: string;
};

export type PlanOrder = {
  symbol: string;
  label: string;
  side: "buy" | "sell";
  qty: number;
  price: number;
  notional: number;
  reason: string;
  note: string;
};

export type Holding = {
  symbol: string;
  label: string;
  qty: number;
  entry_price: number;
  price: number;
  value: number;
  pnl_usd: number;
  pnl_pct: number;
  from_peak_pct: number;
  held_sessions: number;
};

export type PlanPreview = {
  session: string;
  rebalance: boolean;
  live: boolean;
  skipped: string | null;
  equity: number;
  cash: number;
  orders: PlanOrder[];
  notes: Record<string, string>;
  holdings: Holding[];
  error?: string;
};

export type PaperOrder = {
  id: number;
  symbol: string;
  label: string;
  side: "buy" | "sell";
  qty: number;
  status: string;
  filled_qty: number;
  filled_avg_price: number | null;
  requested_price: number | null;
  realized_pnl: number | null;
  reason: string;
  broker: string;
  broker_order_id: string | null;
  created_at: string;
  purpose?: "trade" | "safety_stop";
  order_type?: string;
  stop_price?: number | null;
};

export type CostStats = { n: number; avg_bps: number | null; median_bps: number | null; weighted_bps: number | null; cost_usd: number };

export type Feedback = {
  available: boolean;
  assumed_cost_bps?: number;
  costs?: { in_session: CostStats; overnight: CostStats; recent: { id: number; symbol: string; side: string; bps: number; usd: number; in_session: boolean; at: string }[] };
  gap?: { paper_pnl_usd: number; replay_pnl_usd: number; gap_usd: number; execution_usd: number; other_usd: number };
  suggestions?: { level: "info" | "action" | "warning"; text: string; suggested_cost_bps?: number }[];
};

export type PaperDetail = {
  model: Model;
  now: PlanPreview;
  curve: { dates: string[]; equity: number[] };
  replay: { available: boolean; reason?: string; dates?: string[]; equity?: number[]; pnl_usd?: number; max_drawdown_usd?: number };
  orders: PaperOrder[];
  events: LabEvent[];
  feedback?: Feedback;
};

// ------------------------------------------------------------------ display --

export const PROFILE_META: Record<
  ProfileKey,
  { Icon: LucideIcon; hex: string; chip: string; soft: string; text: string; ring: string; bar: string }
> = {
  flambeur: {
    Icon: Dices,
    hex: "#d97706",
    chip: "bg-amber-500/10 text-amber-700 ring-amber-500/20",
    soft: "bg-amber-500/10",
    text: "text-amber-700",
    ring: "hover:ring-amber-500/40",
    bar: "bg-amber-500",
  },
  matheux: {
    Icon: Sigma,
    hex: "#2563eb",
    chip: "bg-blue-500/10 text-blue-700 ring-blue-500/20",
    soft: "bg-blue-500/10",
    text: "text-blue-700",
    ring: "hover:ring-blue-500/40",
    bar: "bg-blue-500",
  },
  stratege: {
    Icon: Compass,
    hex: "#059669",
    chip: "bg-emerald-500/10 text-emerald-700 ring-emerald-500/20",
    soft: "bg-emerald-500/10",
    text: "text-emerald-700",
    ring: "hover:ring-emerald-500/40",
    bar: "bg-emerald-500",
  },
};

export const PROFILE_ORDER: ProfileKey[] = ["flambeur", "matheux", "stratege"];

const n0 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });
const n2 = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Money the way the owner reads it: "+2 340 $", "−1 100 $". */
export function usd(v: number | null | undefined, opts: { signed?: boolean; cents?: boolean } = {}) {
  if (v == null || Number.isNaN(v)) return "—";
  const body = (opts.cents ? n2 : n0).format(Math.abs(v));
  const sign = v < 0 ? "−" : opts.signed && v > 0 ? "+" : "";
  return `${sign}${body} $`;
}

export function pct(v: number | null | undefined, opts: { signed?: boolean; digits?: number } = {}) {
  if (v == null || Number.isNaN(v)) return "—";
  const digits = opts.digits ?? 1;
  const sign = v < 0 ? "−" : opts.signed !== false && v > 0 ? "+" : "";
  return `${sign}${Math.abs(v).toFixed(digits).replace(".", ",")} %`;
}

export function tone(v: number | null | undefined) {
  if (v == null || v === 0) return "text-muted-foreground";
  return v > 0 ? "text-emerald-600" : "text-red-600";
}

export function qty(v: number) {
  return v >= 100 ? n0.format(v) : v.toLocaleString("fr-FR", { maximumFractionDigits: 4 });
}

export function ago(iso: string) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "à l'instant";
  if (s < 3600) return `il y a ${Math.floor(s / 60)} min`;
  if (s < 86400) return `il y a ${Math.floor(s / 3600)} h`;
  return new Date(iso).toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
}

export const STAGE_LABEL: Record<Stage, string> = { lab: "Labo", paper: "En paper", retired: "Retiré" };

export type OptimizeResult = {
  params: Record<string, unknown>;
  in_sample_pnl_usd: number;
  in_sample_drawdown_usd: number;
  out_of_sample_pnl_usd: number;
  out_of_sample_drawdown_usd: number;
  out_of_sample_vs_placebo_usd: number;
  out_of_sample_vs_spy_usd: number;
  placebo_oos_usd: number;
  spy_oos_usd: number;
  sells: number;
};

export type OptimizeJob = {
  id: string;
  profile: ProfileKey;
  start: string;
  split: string;
  budget: number;
  total: number;
  done: number;
  status: "running" | "done" | "failed";
  error: string | null;
  elapsed_s: number;
  results?: OptimizeResult[];
  verdict: {
    combinations?: number;
    rank_correlation?: number;
    reading?: string;
    best_in_sample?: { params: Record<string, unknown>; in_sample_pnl_usd: number; out_of_sample_pnl_usd: number; out_of_sample_rank: number };
    top5_in_sample_mean_oos_usd?: number;
    median_oos_usd?: number;
    placebo_oos_usd?: number;
    spy_oos_usd?: number;
    share_beating_placebo_oos_pct?: number;
  };
};

export type Exposure = {
  total: number;
  threshold_pct: number;
  symbols: { symbol: string; label: string; sector: string; held: number; pending: number; value: number; pct: number; models: string[] }[];
  sectors: { sector: string; value: number; pct: number; symbols: number; models: number }[];
  alerts: { sector: string; pct: number; models: number }[];
};
