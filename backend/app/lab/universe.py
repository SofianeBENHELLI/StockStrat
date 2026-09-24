"""What each profile is allowed to trade.

Two honest caveats live here, because this is where they originate:

- **Survivorship bias.** The equity universe is today's US mega-caps. Run over
  ten years, a backtest on it only ever holds companies that went on to become
  giants, and never one that collapsed out of the index. Results on this list
  are flattered; the lab says so next to every equity backtest.
- **Theme ETFs have different inception dates.** XLC exists since mid-2018; a
  theme simply is not a candidate before its first bar. The engine handles that
  by treating a missing price as "not tradable yet", never by back-filling.
"""
from __future__ import annotations

BENCHMARK = "SPY"

# ~100 of the largest, most liquid US companies. Liquidity matters more than
# breadth here: the Flambeur trades daily and pays the spread every time.
MEGA_CAPS = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "JPM", "JNJ", "V",
    "PG", "UNH", "HD", "MA", "XOM", "CVX", "PFE", "ABBV", "KO", "PEP",
    "MRK", "LLY", "AVGO", "COST", "WMT", "MCD", "CSCO", "ACN", "ADBE", "CRM",
    "NFLX", "INTC", "AMD", "QCOM", "TXN", "ORCL", "IBM", "NKE", "DIS", "BAC",
    "WFC", "C", "GS", "MS", "AXP", "BLK", "SCHW", "CAT", "DE", "HON",
    "GE", "MMM", "UPS", "UNP", "LMT", "RTX", "BA", "NOC", "GD", "T",
    "VZ", "CMCSA", "TMO", "DHR", "ABT", "MDT", "BMY", "AMGN", "GILD", "ISRG",
    "SYK", "CVS", "CI", "ELV", "LOW", "TGT", "SBUX", "BKNG", "NEE", "DUK",
    "SO", "AMT", "PLD", "SPG", "LIN", "APD", "COP", "SLB", "EOG", "MO",
    "PM", "MDLZ", "CL", "KMB", "GIS", "ADP", "INTU", "AMAT", "LRCX", "MU",
]

# Themes the Stratège can hold, each a liquid ETF. The label is what the UI
# shows, because "XLE" means nothing to most people and "Pétrole & gaz" does.
THEMES: dict[str, str] = {
    "XLE": "Pétrole & gaz",
    "XLK": "Technologie",
    "SMH": "Semi-conducteurs",
    "XLF": "Banques & finance",
    "XLV": "Santé",
    "IBB": "Biotech",
    "XLI": "Industrie",
    "ITA": "Défense & aéro",
    "XLU": "Services publics",
    "XLP": "Consommation de base",
    "XLY": "Consommation discrétionnaire",
    "XLB": "Matériaux",
    "COPX": "Cuivre & mines",
    "URA": "Uranium",
    "ICLN": "Énergie propre",
    "XLRE": "Immobilier",
    "XLC": "Communication",
    "GLD": "Or",
    "TLT": "Obligations US longues",
}

ALL_STOCKS = sorted(set(MEGA_CAPS) | set(THEMES) | {BENCHMARK})


def label(symbol: str) -> str:
    return THEMES.get(symbol, symbol)
