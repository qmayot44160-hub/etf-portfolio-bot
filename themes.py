"""
Module Discover Themes - baskets thématiques multi-classes.

Chaque thème regroupe 4-8 tickers (ETF, Actions directes, Crypto, Matières)
autour d'un sujet d'investissement. L'utilisateur peut scanner un thème
pour appliquer le scoring Smart Picks uniquement sur son univers.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import List, Dict, Optional

from data_paths import data_path


# ──────────────────────────────────────────────────────────
#  Définition des 6 thèmes (multi-classes)
# ──────────────────────────────────────────────────────────

THEMES: Dict[str, dict] = {
    "ai": {
        "id": "ai",
        "name": "Intelligence Artificielle",
        "short_name": "IA & Semi-conducteurs",
        "emoji": "🤖",
        "color": "#0a84ff",
        "gradient": ["#0a84ff", "#5ac8fa"],
        "description": "L'essor de l'IA et de l'infrastructure qui la fait tourner : GPU, cloud, data centers, modèles de frontière.",
        "tickers": [
            {"ticker": "NVDA",    "name": "NVIDIA",                  "asset_class": "actions", "category": "Semi-conducteurs"},
            {"ticker": "ASML",    "name": "ASML Holding",            "asset_class": "actions", "category": "Semi-conducteurs"},
            {"ticker": "MSFT",    "name": "Microsoft",               "asset_class": "actions", "category": "Cloud/IA"},
            {"ticker": "GOOGL",   "name": "Alphabet",                "asset_class": "actions", "category": "IA"},
            {"ticker": "TSM",     "name": "Taiwan Semiconductor",    "asset_class": "actions", "category": "Fonderie"},
            {"ticker": "SOXX",    "name": "iShares Semiconductor",   "asset_class": "etf",     "category": "ETF Tech"},
            {"ticker": "BOTZ",    "name": "Global X Robotics & AI",  "asset_class": "etf",     "category": "ETF IA/Robotique"},
            {"ticker": "QQQ",     "name": "Invesco NASDAQ-100",      "asset_class": "etf",     "category": "ETF Tech US"},
        ],
    },
    "clean_energy": {
        "id": "clean_energy",
        "name": "Transition énergétique",
        "short_name": "Énergie propre",
        "emoji": "☀️",
        "color": "#30d158",
        "gradient": ["#30d158", "#66e896"],
        "description": "Solaire, éolien, batteries et réseaux électriques de nouvelle génération.",
        "tickers": [
            {"ticker": "ICLN",    "name": "iShares Global Clean Energy", "asset_class": "etf",     "category": "ETF Clean Energy"},
            {"ticker": "TAN",     "name": "Invesco Solar ETF",           "asset_class": "etf",     "category": "ETF Solaire"},
            {"ticker": "QCLN",    "name": "First Trust Clean Edge",      "asset_class": "etf",     "category": "ETF Clean Tech"},
            {"ticker": "ENPH",    "name": "Enphase Energy",              "asset_class": "actions", "category": "Solaire"},
            {"ticker": "FSLR",    "name": "First Solar",                 "asset_class": "actions", "category": "Solaire"},
            {"ticker": "TSLA",    "name": "Tesla",                       "asset_class": "actions", "category": "EV/Batteries"},
            {"ticker": "LIT",     "name": "Global X Lithium & Battery",  "asset_class": "etf",     "category": "ETF Lithium"},
        ],
    },
    "dividends": {
        "id": "dividends",
        "name": "Dividendes de qualité",
        "short_name": "Dividendes",
        "emoji": "💰",
        "color": "#bf5af2",
        "gradient": ["#bf5af2", "#8e6aff"],
        "description": "Actions et ETF à dividendes croissants : cash flow stable, sociétés matures.",
        "tickers": [
            {"ticker": "SCHD",    "name": "Schwab US Dividend Equity",   "asset_class": "etf",     "category": "ETF Dividendes US"},
            {"ticker": "VYM",     "name": "Vanguard High Dividend",      "asset_class": "etf",     "category": "ETF Dividendes"},
            {"ticker": "DGRO",    "name": "iShares Core Dividend Growth","asset_class": "etf",     "category": "ETF Dividend Growth"},
            {"ticker": "NOBL",    "name": "ProShares Dividend Aristocrats","asset_class": "etf",   "category": "ETF Aristocrats"},
            {"ticker": "JNJ",     "name": "Johnson & Johnson",           "asset_class": "actions", "category": "Santé"},
            {"ticker": "KO",      "name": "Coca-Cola",                   "asset_class": "actions", "category": "Consommation"},
            {"ticker": "PG",      "name": "Procter & Gamble",            "asset_class": "actions", "category": "Consommation"},
        ],
    },
    "btc_ecosystem": {
        "id": "btc_ecosystem",
        "name": "Écosystème Bitcoin",
        "short_name": "Bitcoin & Crypto",
        "emoji": "₿",
        "color": "#ff9f0a",
        "gradient": ["#ff9f0a", "#ffd60a"],
        "description": "Bitcoin, Ethereum et les acteurs de l'infrastructure crypto (ETF spot, miners, exchanges).",
        "tickers": [
            {"ticker": "BTC-USD", "name": "Bitcoin",                     "asset_class": "crypto",  "category": "L1"},
            {"ticker": "ETH-USD", "name": "Ethereum",                    "asset_class": "crypto",  "category": "L1"},
            {"ticker": "SOL-USD", "name": "Solana",                      "asset_class": "crypto",  "category": "L1"},
            {"ticker": "IBIT",    "name": "iShares Bitcoin Trust",       "asset_class": "etf",     "category": "ETF BTC spot"},
            {"ticker": "FBTC",    "name": "Fidelity Wise Origin Bitcoin","asset_class": "etf",     "category": "ETF BTC spot"},
            {"ticker": "COIN",    "name": "Coinbase",                    "asset_class": "actions", "category": "Exchange"},
            {"ticker": "MSTR",    "name": "MicroStrategy",               "asset_class": "actions", "category": "BTC treasury"},
            {"ticker": "MARA",    "name": "Marathon Digital",            "asset_class": "actions", "category": "Mining"},
        ],
    },
    "emerging": {
        "id": "emerging",
        "name": "Marchés émergents",
        "short_name": "Émergents",
        "emoji": "🌏",
        "color": "#64d2ff",
        "gradient": ["#64d2ff", "#5ab0ff"],
        "description": "Inde, Chine, Asie du Sud-Est, Amérique latine : croissance démographique et rattrapage économique.",
        "tickers": [
            {"ticker": "EEM",     "name": "iShares MSCI Emerging",       "asset_class": "etf",     "category": "ETF Émergents"},
            {"ticker": "VWO",     "name": "Vanguard FTSE Emerging",      "asset_class": "etf",     "category": "ETF Émergents"},
            {"ticker": "INDA",    "name": "iShares MSCI India",          "asset_class": "etf",     "category": "ETF Inde"},
            {"ticker": "MCHI",    "name": "iShares MSCI China",          "asset_class": "etf",     "category": "ETF Chine"},
            {"ticker": "FXI",     "name": "iShares China Large-Cap",     "asset_class": "etf",     "category": "ETF Chine"},
            {"ticker": "EWZ",     "name": "iShares MSCI Brazil",         "asset_class": "etf",     "category": "ETF Brésil"},
            {"ticker": "EWT",     "name": "iShares MSCI Taiwan",         "asset_class": "etf",     "category": "ETF Taïwan"},
        ],
    },
    "bonds_safe": {
        "id": "bonds_safe",
        "name": "Obligations & Or",
        "short_name": "Refuge",
        "emoji": "🛡️",
        "color": "#ffd60a",
        "gradient": ["#ffd60a", "#ffcc00"],
        "description": "Obligations d'État, high-grade credit et or - le côté défensif du portefeuille.",
        "tickers": [
            {"ticker": "TLT",     "name": "iShares 20+ Year Treasury",   "asset_class": "etf",        "category": "ETF T-Bonds long"},
            {"ticker": "IEF",     "name": "iShares 7-10 Year Treasury",  "asset_class": "etf",        "category": "ETF T-Bonds moyen"},
            {"ticker": "SHY",     "name": "iShares 1-3 Year Treasury",   "asset_class": "etf",        "category": "ETF T-Bonds court"},
            {"ticker": "AGG",     "name": "iShares Core US Aggregate",   "asset_class": "etf",        "category": "ETF Obligations"},
            {"ticker": "LQD",     "name": "iShares Investment Grade",    "asset_class": "etf",        "category": "ETF Credit IG"},
            {"ticker": "GLD",     "name": "SPDR Gold Shares",            "asset_class": "commodity",  "category": "Or"},
            {"ticker": "IAU",     "name": "iShares Gold Trust",          "asset_class": "commodity",  "category": "Or"},
            {"ticker": "SLV",     "name": "iShares Silver Trust",        "asset_class": "commodity",  "category": "Argent"},
        ],
    },
}


# ──────────────────────────────────────────────────────────
#  Cache perf (évite yfinance à chaque requête UI)
# ──────────────────────────────────────────────────────────

_PERF_CACHE: Dict[str, dict] = {}
_PERF_CACHE_TS: float = 0
_PERF_CACHE_TTL = 3600  # 1 h
_PERF_LOCK = threading.Lock()


def _yf_symbol(tk: dict) -> str:
    t = tk["ticker"].upper()
    if tk.get("asset_class") == "crypto" and not t.endswith("-USD"):
        base = t.replace("USDT", "").replace("USD", "").replace("/", "")
        return f"{base}-USD"
    return t


def _compute_theme_perf(theme: dict) -> dict:
    """Retourne {perf_1m, perf_3m, perf_1y, avg_score} du thème."""
    try:
        import yfinance as yf
    except ImportError:
        return {"perf_1m": None, "perf_3m": None, "perf_1y": None}

    yf_syms = [_yf_symbol(t) for t in theme["tickers"]]
    try:
        data = yf.download(
            tickers=list(set(yf_syms)),
            period="1y", interval="1d",
            progress=False, auto_adjust=True, threads=True,
        )
    except Exception as e:
        print(f"[themes] yf error {theme['id']}: {e}")
        return {"perf_1m": None, "perf_3m": None, "perf_1y": None}

    if data is None or len(data) == 0:
        return {"perf_1m": None, "perf_3m": None, "perf_1y": None}

    try:
        if hasattr(data.columns, "levels"):
            closes = data["Close"]
        else:
            closes = data[["Close"]]
            closes.columns = [yf_syms[0]]
    except Exception:
        return {"perf_1m": None, "perf_3m": None, "perf_1y": None}

    def _avg_perf(offset_days: int) -> Optional[float]:
        perfs = []
        for sym in yf_syms:
            try:
                if sym not in closes.columns:
                    continue
                s = closes[sym].dropna()
                if len(s) < offset_days + 1:
                    continue
                past = float(s.iloc[-offset_days - 1])
                now = float(s.iloc[-1])
                if past > 0:
                    perfs.append((now - past) / past * 100)
            except Exception:
                continue
        return round(sum(perfs) / len(perfs), 2) if perfs else None

    return {
        "perf_1m": _avg_perf(21),
        "perf_3m": _avg_perf(63),
        "perf_1y": _avg_perf(252),
    }


def get_themes_with_perf(force_refresh: bool = False) -> List[dict]:
    """Liste des 6 thèmes enrichis avec les perfs moyennes. Cache 1h."""
    global _PERF_CACHE, _PERF_CACHE_TS
    now = time.time()

    with _PERF_LOCK:
        if not force_refresh and _PERF_CACHE and (now - _PERF_CACHE_TS) < _PERF_CACHE_TTL:
            return [_theme_summary(t, _PERF_CACHE.get(t["id"], {})) for t in THEMES.values()]

    # Compute in background for all themes
    computed = {}
    for theme_id, theme in THEMES.items():
        computed[theme_id] = _compute_theme_perf(theme)

    with _PERF_LOCK:
        _PERF_CACHE = computed
        _PERF_CACHE_TS = now

    return [_theme_summary(t, computed.get(t["id"], {})) for t in THEMES.values()]


def _theme_summary(theme: dict, perf: dict) -> dict:
    """Sérialisation pour l'UI (sans exposer la liste complète dans le listing)."""
    counts = {"etf": 0, "actions": 0, "crypto": 0, "commodity": 0}
    for tk in theme["tickers"]:
        cls = tk.get("asset_class", "etf")
        counts[cls] = counts.get(cls, 0) + 1
    return {
        "id": theme["id"],
        "name": theme["name"],
        "short_name": theme["short_name"],
        "emoji": theme["emoji"],
        "color": theme["color"],
        "gradient": theme["gradient"],
        "description": theme["description"],
        "tickers_preview": [t["ticker"] for t in theme["tickers"][:5]],
        "tickers_count": len(theme["tickers"]),
        "counts": counts,
        "perf_1m": perf.get("perf_1m"),
        "perf_3m": perf.get("perf_3m"),
        "perf_1y": perf.get("perf_1y"),
    }


# ──────────────────────────────────────────────────────────
#  Détail thème + scan Smart Picks filtré
# ──────────────────────────────────────────────────────────

def get_theme_detail(theme_id: str) -> Optional[dict]:
    theme = THEMES.get(theme_id)
    if not theme:
        return None

    # Perf + prix courant pour chaque ticker
    try:
        import yfinance as yf
    except ImportError:
        return _theme_summary(theme, {}) | {"tickers": theme["tickers"]}

    yf_map = {t["ticker"]: _yf_symbol(t) for t in theme["tickers"]}
    try:
        data = yf.download(
            tickers=list(set(yf_map.values())),
            period="3mo", interval="1d",
            progress=False, auto_adjust=True, threads=True,
        )
    except Exception:
        data = None

    enriched_tickers = []
    closes = None
    if data is not None and len(data) > 0:
        try:
            if hasattr(data.columns, "levels"):
                closes = data["Close"]
            else:
                closes = data[["Close"]]
                closes.columns = [list(yf_map.values())[0]]
        except Exception:
            closes = None

    for tk in theme["tickers"]:
        out = dict(tk)
        yf_sym = yf_map[tk["ticker"]]
        if closes is not None and yf_sym in closes.columns:
            try:
                s = closes[yf_sym].dropna()
                if len(s) >= 2:
                    current = float(s.iloc[-1])
                    out["price"] = round(current, 4)
                    out["change_1d"] = round((current - float(s.iloc[-2])) / float(s.iloc[-2]) * 100, 2)
                    if len(s) >= 21:
                        out["change_1m"] = round((current - float(s.iloc[-22])) / float(s.iloc[-22]) * 100, 2)
                    if len(s) >= 63:
                        out["change_3m"] = round((current - float(s.iloc[-64])) / float(s.iloc[-64]) * 100, 2)
                    out["sparkline"] = [round(float(v), 4) for v in s.iloc[-30:].tolist()]
                    # Score simple : momentum combiné
                    score_components = []
                    for k, weight in [("change_1d", 0.1), ("change_1m", 0.4), ("change_3m", 0.5)]:
                        v = out.get(k)
                        if v is not None:
                            score_components.append(max(0, min(100, 50 + v * 1.8)) * weight)
                    if score_components:
                        out["score"] = round(sum(score_components) / sum(w for _, w in [("change_1d", 0.1), ("change_1m", 0.4), ("change_3m", 0.5)] if out.get(_) is not None), 1)
                        out["direction"] = "BULL" if (out.get("change_1m") or 0) > 0 else "BEAR"
            except Exception:
                pass
        enriched_tickers.append(out)

    # Tri par score décroissant
    enriched_tickers.sort(key=lambda x: x.get("score") or 0, reverse=True)

    perf = {
        "perf_1m": None, "perf_3m": None, "perf_1y": None,
    }
    # Recalcule moyennes depuis les enriched (optimise : pas de 2e yf call)
    for key in ("change_1m", "change_3m"):
        vals = [t.get(key) for t in enriched_tickers if t.get(key) is not None]
        if vals:
            perf["perf_" + key.split("_")[1]] = round(sum(vals) / len(vals), 2)
    # perf_1y non calculé ici (on a 3mo period) - fallback cache
    with _PERF_LOCK:
        cached = _PERF_CACHE.get(theme_id, {})
    if cached.get("perf_1y") is not None:
        perf["perf_1y"] = cached["perf_1y"]

    summary = _theme_summary(theme, perf)
    summary["tickers"] = enriched_tickers
    summary["top_picks"] = [t for t in enriched_tickers if t.get("score")][:5]
    return summary
