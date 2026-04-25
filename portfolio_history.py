"""
Historique de la valeur du portefeuille global (toutes classes d'actifs).

Stocke des snapshots {timestamp, total, etf, actions, crypto, commodity, cash}
dans un fichier JSON, et offre une reconstruction rétroactive via yfinance
pour afficher un chart même quand le bot vient d'être lancé.

Utilisé par /api/portfolio/history?range=1d|1w|1m|3m|1y|all
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

from data_paths import data_path


HISTORY_FILE = data_path("portfolio_history.json")
MAX_SNAPSHOTS = 2000  # ~5 ans à 1 snapshot/jour

_LOCK = threading.Lock()

# Tickers classés comme "matières premières"
_COMMODITY_TICKERS = {
    "GLD", "SLV", "USO", "DBC", "PDBC", "IAU", "SGOL", "UNG", "BNO",
    "WEAT", "CORN", "DBO", "PALL", "PPLT", "CPER",
}
_COMMODITY_KEYWORDS = [
    "or ", "gold", "silver", "argent", "pétrole", "petrole", "oil", "brent", "wti",
    "gaz", "natural gas", "commodit", "matière", "matiere", "énergie", "energie",
    "métaux", "metaux",
]


# ──────────────────────────────────────────────────────────
#  Classification
# ──────────────────────────────────────────────────────────

def classify_position(pos: dict, default_class: str = "etf") -> str:
    """Retourne 'actions' | 'etf' | 'crypto' | 'commodity'."""
    if default_class == "crypto":
        return "crypto"

    ticker = (pos.get("ticker") or pos.get("symbol") or "").upper()
    cat = (pos.get("category") or "").lower()
    name = (pos.get("name") or "").lower()
    blob = cat + " " + name

    if ticker in _COMMODITY_TICKERS:
        return "commodity"
    if any(kw in blob for kw in _COMMODITY_KEYWORDS):
        return "commodity"

    asset_class = (pos.get("asset_class") or pos.get("type") or "").lower()
    if asset_class in ("stock", "action", "equity_direct"):
        return "actions"

    return "etf"


def classify_portfolio(pf: Optional[dict], crypto_pf: Optional[dict]) -> Dict[str, float]:
    """Agrège la valeur par classe à partir des deux dicts portfolio."""
    out = {"actions": 0.0, "etf": 0.0, "crypto": 0.0, "commodity": 0.0, "cash": 0.0}

    for p in (pf or {}).get("positions", []) or []:
        cls = classify_position(p, "etf")
        out[cls] += float(p.get("market_value") or p.get("value") or 0)
    for p in (crypto_pf or {}).get("positions", []) or []:
        out["crypto"] += float(p.get("market_value") or p.get("value") or 0)

    out["cash"] = float((pf or {}).get("cash") or 0) + float((crypto_pf or {}).get("cash") or 0)
    return out


# ──────────────────────────────────────────────────────────
#  Snapshots persistants
# ──────────────────────────────────────────────────────────

def _load() -> List[dict]:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(snapshots: List[dict]) -> None:
    try:
        # Cap à MAX_SNAPSHOTS
        if len(snapshots) > MAX_SNAPSHOTS:
            snapshots = snapshots[-MAX_SNAPSHOTS:]
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(snapshots, f, indent=2)
    except Exception as e:
        print(f"[portfolio_history] save error: {e}")


def snapshot(pf: Optional[dict], crypto_pf: Optional[dict],
             dedupe_same_day: bool = True) -> dict:
    """Écrit un snapshot maintenant ; retourne le snapshot créé."""
    with _LOCK:
        breakdown = classify_portfolio(pf, crypto_pf)
        total = breakdown["actions"] + breakdown["etf"] + breakdown["crypto"] + breakdown["commodity"]
        snap = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "total": round(total + breakdown["cash"], 2),
            "invested": round(total, 2),
            "etf": round(breakdown["etf"], 2),
            "actions": round(breakdown["actions"], 2),
            "crypto": round(breakdown["crypto"], 2),
            "commodity": round(breakdown["commodity"], 2),
            "cash": round(breakdown["cash"], 2),
        }

        snapshots = _load()
        if dedupe_same_day and snapshots:
            last = snapshots[-1]
            try:
                last_day = last["ts"][:10]
                if last_day == snap["ts"][:10]:
                    # Remplace le snapshot du jour
                    snapshots[-1] = snap
                    _save(snapshots)
                    return snap
            except Exception:
                pass

        snapshots.append(snap)
        _save(snapshots)
        return snap


# ──────────────────────────────────────────────────────────
#  Requête historique
# ──────────────────────────────────────────────────────────

_RANGE_TO_DAYS = {
    "1d": 1, "1w": 7, "1m": 30, "3m": 90, "6m": 180, "1y": 365, "all": 99999,
}


def _cutoff(range_key: str) -> datetime:
    days = _RANGE_TO_DAYS.get(range_key.lower(), 30)
    return datetime.utcnow() - timedelta(days=days)


def get_history(range_key: str = "1m") -> List[dict]:
    """Snapshots persistants filtrés sur la période."""
    snaps = _load()
    if not snaps:
        return []
    cutoff = _cutoff(range_key)
    out = []
    for s in snaps:
        try:
            ts = datetime.fromisoformat(s["ts"].rstrip("Z"))
            if ts >= cutoff:
                out.append(s)
        except Exception:
            continue
    return out


# ──────────────────────────────────────────────────────────
#  Reconstruction rétroactive via yfinance
# ──────────────────────────────────────────────────────────

def reconstruct_history(pf: Optional[dict], crypto_pf: Optional[dict],
                        range_key: str = "1m") -> List[dict]:
    """
    Reconstruit une série historique *synthétique* en supposant que les
    positions actuelles étaient détenues sur toute la période.
    Utile quand on n'a pas encore assez de snapshots.
    """
    positions = []
    for p in (pf or {}).get("positions", []) or []:
        ticker = p.get("ticker") or p.get("symbol")
        shares = float(p.get("shares") or p.get("quantity") or 0)
        if ticker and shares > 0:
            positions.append({"ticker": ticker, "shares": shares,
                              "class": classify_position(p, "etf")})
    for p in (crypto_pf or {}).get("positions", []) or []:
        ticker = p.get("ticker") or p.get("symbol")
        shares = float(p.get("shares") or p.get("quantity") or 0)
        if ticker and shares > 0:
            positions.append({"ticker": ticker, "shares": shares, "class": "crypto"})

    if not positions:
        return []

    try:
        import yfinance as yf
    except ImportError:
        return []

    days = _RANGE_TO_DAYS.get(range_key.lower(), 30)
    if days >= 365:
        period, interval = "1y", "1d"
    elif days >= 90:
        period, interval = "3mo", "1d"
    elif days >= 30:
        period, interval = "1mo", "1d"
    elif days >= 7:
        period, interval = "7d", "1h"
    else:
        period, interval = "1d", "15m"

    cash = float((pf or {}).get("cash") or 0) + float((crypto_pf or {}).get("cash") or 0)

    # Batch download
    tickers_for_yf = []
    for p in positions:
        t = p["ticker"]
        # Crypto Binance → yfinance symbole approximatif
        if p["class"] == "crypto" and not t.endswith("-USD"):
            base = t.replace("USDT", "").replace("USD", "").replace("/", "")
            t_yf = f"{base}-USD"
        else:
            t_yf = t
        p["yf"] = t_yf
        tickers_for_yf.append(t_yf)

    try:
        data = yf.download(
            tickers=list(set(tickers_for_yf)),
            period=period, interval=interval,
            progress=False, auto_adjust=True, threads=True,
        )
    except Exception as e:
        print(f"[portfolio_history] yf.download error: {e}")
        return []

    if data is None or len(data) == 0:
        return []

    # Extraction Close multi-index vs single
    try:
        if isinstance(data.columns, type(data.columns)) and hasattr(data.columns, "levels"):
            closes = data["Close"]
        else:
            closes = data[["Close"]].rename(columns={"Close": tickers_for_yf[0]})
    except Exception:
        closes = data.get("Close", data)

    # Construit la série temporelle
    try:
        series = []
        for ts, row in closes.iterrows():
            total_invested = 0.0
            by_class = {"actions": 0.0, "etf": 0.0, "crypto": 0.0, "commodity": 0.0}
            skip = False
            for p in positions:
                yf_sym = p["yf"]
                try:
                    # row peut être Series (multi-ticker) ou scalar (1 ticker)
                    price = row[yf_sym] if hasattr(row, "__getitem__") else row
                except Exception:
                    price = None
                if price is None or price != price:  # NaN check
                    continue
                val = float(price) * p["shares"]
                total_invested += val
                by_class[p["class"]] += val
            if total_invested <= 0:
                continue
            series.append({
                "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                "total": round(total_invested + cash, 2),
                "invested": round(total_invested, 2),
                "etf": round(by_class["etf"], 2),
                "actions": round(by_class["actions"], 2),
                "crypto": round(by_class["crypto"], 2),
                "commodity": round(by_class["commodity"], 2),
                "cash": round(cash, 2),
            })
        return series
    except Exception as e:
        print(f"[portfolio_history] reconstruct error: {e}")
        return []


def get_history_or_reconstruct(pf: Optional[dict], crypto_pf: Optional[dict],
                               range_key: str = "1m") -> Tuple[List[dict], str]:
    """
    Retourne (series, source) où source ∈ {"snapshots", "reconstructed", "empty"}.
    Privilégie les snapshots réels si on en a au moins 3 sur la période.
    """
    snaps = get_history(range_key)
    if len(snaps) >= 3:
        return snaps, "snapshots"

    reconstructed = reconstruct_history(pf, crypto_pf, range_key)
    if reconstructed:
        return reconstructed, "reconstructed"

    # Fallback : snapshot actuel seul
    current = snapshot(pf, crypto_pf)
    return [current], "empty"


# ──────────────────────────────────────────────────────────
#  Benchmark (SPY, MSCI World) - overlay chart
# ──────────────────────────────────────────────────────────

def get_benchmark_series(benchmark: str = "SPY", range_key: str = "1m") -> List[dict]:
    """Retourne la série normalisée 100=début pour overlay chart."""
    try:
        import yfinance as yf
    except ImportError:
        return []

    days = _RANGE_TO_DAYS.get(range_key.lower(), 30)
    if days >= 365:
        period, interval = "1y", "1d"
    elif days >= 90:
        period, interval = "3mo", "1d"
    elif days >= 30:
        period, interval = "1mo", "1d"
    elif days >= 7:
        period, interval = "7d", "1h"
    else:
        period, interval = "1d", "15m"

    try:
        data = yf.download(benchmark, period=period, interval=interval,
                           progress=False, auto_adjust=True)
        if data is None or len(data) == 0:
            return []
        closes = data["Close"].dropna()
        if len(closes) == 0:
            return []
        base = float(closes.iloc[0])
        return [
            {"ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
             "value": round(float(c) / base * 100, 3)}
            for ts, c in closes.items()
        ]
    except Exception as e:
        print(f"[portfolio_history] benchmark error: {e}")
        return []
