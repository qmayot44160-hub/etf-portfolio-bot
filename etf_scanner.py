"""
Scanner ETF autonome — le bot choisit lui-même les meilleurs ETF du moment.

Pipeline (inspiré de market_scanner.py mais pour le marché actions/ETF):
1. Univers curaté de ~40 ETF populaires (Monde, US, Tech, Sectors, Commodities, Bonds)
2. Télécharge 90j d'historique via yfinance (batch)
3. Scoring multi-facteur :
   - Momentum 30j (pondération 35%)
   - Trend strength (SMA20 vs SMA50) (25%)
   - Volume surge (récent vs moyen) (20%)
   - Risk-adjusted return (Sharpe simplifié 30j) (20%)
4. Cache les résultats, auto-refresh en background thread

Design identique à market_scanner :
- Classe ETFScanner avec scan() / get_cached_results()
- Cache JSON persisté
- Thread-safe
"""

import os
import json
import time
import threading
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Optional, List

from data_paths import data_path

ETF_SCANNER_CACHE = data_path("etf_scanner_cache.json")

# Univers curaté — tickers yfinance (US + EU). Mélange délibéré pour la découverte.
ETF_UNIVERSE = [
    # Core global / broad
    {"ticker": "IWDA.AS",  "name": "iShares MSCI World",         "category": "Monde"},
    {"ticker": "VWCE.DE",  "name": "Vanguard FTSE All-World",    "category": "Monde"},
    {"ticker": "SPY",      "name": "SPDR S&P 500",               "category": "US"},
    {"ticker": "VOO",      "name": "Vanguard S&P 500",           "category": "US"},
    {"ticker": "QQQ",      "name": "Invesco Nasdaq-100",         "category": "Tech US"},
    {"ticker": "PANX.PA",  "name": "Amundi Nasdaq-100",          "category": "Tech US"},
    # Regions
    {"ticker": "EFA",      "name": "iShares MSCI EAFE",          "category": "Développés ex-US"},
    {"ticker": "EEM",      "name": "iShares MSCI Emerging",      "category": "Émergents"},
    {"ticker": "PAEEM.PA", "name": "Amundi MSCI EM",             "category": "Émergents"},
    {"ticker": "FXI",      "name": "iShares China Large-Cap",    "category": "Chine"},
    {"ticker": "EWJ",      "name": "iShares MSCI Japan",         "category": "Japon"},
    {"ticker": "INDA",     "name": "iShares MSCI India",         "category": "Inde"},
    # Sectors
    {"ticker": "XLK",      "name": "Technology Select",          "category": "Tech"},
    {"ticker": "XLF",      "name": "Financials Select",          "category": "Finance"},
    {"ticker": "XLE",      "name": "Energy Select",              "category": "Énergie"},
    {"ticker": "XLV",      "name": "Health Care Select",         "category": "Santé"},
    {"ticker": "XLY",      "name": "Consumer Discretionary",     "category": "Conso cyclique"},
    {"ticker": "XLP",      "name": "Consumer Staples",           "category": "Conso défensive"},
    {"ticker": "XLI",      "name": "Industrials Select",         "category": "Industrie"},
    {"ticker": "XLU",      "name": "Utilities Select",           "category": "Utilities"},
    {"ticker": "XLRE",     "name": "Real Estate Select",         "category": "Immobilier"},
    # Themes
    {"ticker": "SMH",      "name": "VanEck Semiconductors",      "category": "Semi-conducteurs"},
    {"ticker": "ARKK",     "name": "ARK Innovation",             "category": "Innovation"},
    {"ticker": "IBB",      "name": "iShares Biotech",            "category": "Biotech"},
    {"ticker": "ICLN",     "name": "Clean Energy",               "category": "Énergie verte"},
    {"ticker": "ROBO",     "name": "Robotics & AI",              "category": "Robotique"},
    # Factor / style
    {"ticker": "IWM",      "name": "Russell 2000",               "category": "Small Cap US"},
    {"ticker": "VTV",      "name": "Vanguard Value",             "category": "Value"},
    {"ticker": "VUG",      "name": "Vanguard Growth",            "category": "Growth"},
    {"ticker": "MTUM",     "name": "iShares Momentum",           "category": "Momentum"},
    {"ticker": "QUAL",     "name": "iShares Quality",            "category": "Qualité"},
    # Commodities / alt
    {"ticker": "GLD",      "name": "SPDR Gold",                  "category": "Or"},
    {"ticker": "SLV",      "name": "iShares Silver",             "category": "Argent"},
    {"ticker": "USO",      "name": "US Oil Fund",                "category": "Pétrole"},
    {"ticker": "DBC",      "name": "Commodity Broad",            "category": "Matières premières"},
    # Bonds
    {"ticker": "TLT",      "name": "iShares 20+ Year Treasury",  "category": "Obligations LT"},
    {"ticker": "IEF",      "name": "iShares 7-10Y Treasury",     "category": "Obligations MT"},
    {"ticker": "HYG",      "name": "iShares High Yield",         "category": "High Yield"},
    {"ticker": "LQD",      "name": "iShares IG Corporate",       "category": "Corporate IG"},
]


@dataclass
class ETFScanResult:
    ticker: str
    name: str
    category: str
    price: float
    score: float          # 0-100
    direction: str        # "BULL" / "BEAR" / "NEUTRAL"
    change_1d: float
    change_7d: float
    change_30d: float
    volume_surge: float   # ratio volume récent / moyen
    volatility_30d: float
    trend_strength: float # -100 to +100
    sharpe_30d: float
    reasons: List[str] = field(default_factory=list)
    sparkline: List[float] = field(default_factory=list)  # last 30 closes
    timestamp: str = ""


class ETFScanner:
    """Scanner autonome d'ETF — ne nécessite aucun broker, uniquement yfinance."""

    def __init__(self):
        self._lock = threading.Lock()
        self._scanning = False
        self._bg_thread = None
        self.cache = self._load_cache()

    # ─────────────────────────────────────────
    #  Cache I/O
    # ─────────────────────────────────────────
    def _load_cache(self) -> dict:
        if os.path.exists(ETF_SCANNER_CACHE):
            try:
                with open(ETF_SCANNER_CACHE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"results": [], "last_scan": None, "stats": {}}

    def _save_cache(self):
        try:
            with open(ETF_SCANNER_CACHE, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[ETFScanner] cache save error: {e}")

    # ─────────────────────────────────────────
    #  Scan principal
    # ─────────────────────────────────────────
    def scan(self, force: bool = False) -> dict:
        """Scan complet. Idempotent si < 10min depuis le dernier."""
        if self._scanning:
            return {"status": "already_running", "results": self.cache.get("results", [])}

        # Skip si scan très récent et pas force
        if not force and self.cache.get("last_scan"):
            try:
                last = datetime.fromisoformat(self.cache["last_scan"])
                if (datetime.now() - last).total_seconds() < 600:  # 10 min
                    return {"status": "cached", "results": self.cache.get("results", [])}
            except Exception:
                pass

        self._scanning = True
        start = time.time()

        try:
            import yfinance as yf
            import pandas as pd
            import numpy as np

            tickers = [e["ticker"] for e in ETF_UNIVERSE]
            # Batch download 90j d'historique
            data = yf.download(
                tickers, period="90d", progress=False,
                group_by="ticker", threads=True, auto_adjust=True,
            )

            results: List[ETFScanResult] = []
            for etf in ETF_UNIVERSE:
                try:
                    t = etf["ticker"]
                    # Extract per-ticker DataFrame
                    if isinstance(data.columns, pd.MultiIndex):
                        if t not in data.columns.levels[0]:
                            continue
                        df = data[t].dropna()
                    else:
                        df = data.dropna()

                    if len(df) < 30:
                        continue

                    close = df["Close"]
                    vol = df["Volume"] if "Volume" in df.columns else None

                    price = float(close.iloc[-1])

                    # Retours
                    chg_1d = ((close.iloc[-1] / close.iloc[-2]) - 1) * 100 if len(close) > 1 else 0
                    chg_7d = ((close.iloc[-1] / close.iloc[-7]) - 1) * 100 if len(close) > 7 else 0
                    chg_30d = ((close.iloc[-1] / close.iloc[-30]) - 1) * 100 if len(close) > 30 else 0

                    # Volatilité 30j annualisée
                    rets = close.pct_change().dropna().tail(30)
                    vola = float(rets.std() * np.sqrt(252) * 100) if len(rets) > 5 else 0.0

                    # Trend strength : (SMA20 - SMA50) / SMA50 × 100
                    sma20 = close.tail(20).mean() if len(close) >= 20 else close.mean()
                    sma50 = close.tail(50).mean() if len(close) >= 50 else close.mean()
                    trend = float((sma20 - sma50) / sma50 * 100) if sma50 else 0.0

                    # Volume surge : moyenne 5j / moyenne 30j
                    if vol is not None and len(vol) >= 30:
                        v_recent = vol.tail(5).mean()
                        v_avg = vol.tail(30).mean()
                        vsurge = float(v_recent / v_avg) if v_avg else 1.0
                    else:
                        vsurge = 1.0

                    # Sharpe 30j simplifié
                    if len(rets) > 5 and rets.std() > 0:
                        sharpe = float((rets.mean() / rets.std()) * np.sqrt(252))
                    else:
                        sharpe = 0.0

                    # ── Scoring 0-100 ────────────────────────
                    # Momentum 30j (35%) : normalise autour de ±15%
                    s_mom = max(0, min(100, 50 + (chg_30d / 15) * 50)) * 0.35
                    # Trend strength (25%) : ±5% → full range
                    s_trend = max(0, min(100, 50 + (trend / 5) * 50)) * 0.25
                    # Volume surge (20%) : 1.0→50, 2.0→100
                    s_vol = max(0, min(100, (vsurge - 0.5) * 50)) * 0.20
                    # Sharpe (20%) : 0→50, 2→100, -2→0
                    s_sh = max(0, min(100, 50 + sharpe * 25)) * 0.20

                    score = round(s_mom + s_trend + s_vol + s_sh, 1)

                    # Direction
                    if chg_30d > 3 and trend > 0:
                        direction = "BULL"
                    elif chg_30d < -3 and trend < 0:
                        direction = "BEAR"
                    else:
                        direction = "NEUTRAL"

                    # Raisons humaines
                    reasons = []
                    if chg_30d > 8:
                        reasons.append(f"Momentum fort +{chg_30d:.1f}% sur 30j")
                    elif chg_30d < -8:
                        reasons.append(f"Correction -{abs(chg_30d):.1f}% sur 30j (opportunité ?)")
                    if trend > 3:
                        reasons.append("Tendance haussière (SMA20 > SMA50)")
                    elif trend < -3:
                        reasons.append("Tendance baissière (SMA20 < SMA50)")
                    if vsurge > 1.5:
                        reasons.append(f"Volume +{(vsurge-1)*100:.0f}% vs moyenne")
                    if sharpe > 1.5:
                        reasons.append(f"Sharpe 30j excellent ({sharpe:.2f})")
                    elif sharpe < -1.0:
                        reasons.append(f"Sharpe 30j négatif ({sharpe:.2f})")
                    if vola > 40:
                        reasons.append(f"Volatilité élevée ({vola:.0f}%)")
                    if not reasons:
                        reasons.append("Activité normale")

                    # Sparkline : 30 derniers closes
                    spark = [round(float(x), 4) for x in close.tail(30).tolist()]

                    results.append(ETFScanResult(
                        ticker=t,
                        name=etf["name"],
                        category=etf["category"],
                        price=round(price, 2),
                        score=score,
                        direction=direction,
                        change_1d=round(chg_1d, 2),
                        change_7d=round(chg_7d, 2),
                        change_30d=round(chg_30d, 2),
                        volume_surge=round(vsurge, 2),
                        volatility_30d=round(vola, 1),
                        trend_strength=round(trend, 2),
                        sharpe_30d=round(sharpe, 2),
                        reasons=reasons,
                        sparkline=spark,
                        timestamp=datetime.now().isoformat(timespec="seconds"),
                    ))
                except Exception as e:
                    print(f"[ETFScanner] skip {etf['ticker']}: {e}")
                    continue

            # Tri par score
            results.sort(key=lambda r: r.score, reverse=True)

            elapsed = round(time.time() - start, 1)
            self.cache = {
                "results": [asdict(r) for r in results],
                "last_scan": datetime.now().isoformat(timespec="seconds"),
                "stats": {
                    "universe_size": len(ETF_UNIVERSE),
                    "scanned": len(results),
                    "best_ticker": results[0].ticker if results else None,
                    "best_score": results[0].score if results else 0,
                    "duration_s": elapsed,
                },
            }
            self._save_cache()

            return {
                "status": "ok",
                "results": self.cache["results"],
                "stats": self.cache["stats"],
                "last_scan": self.cache["last_scan"],
            }

        except Exception as e:
            print(f"[ETFScanner] scan error: {e}")
            return {"status": "error", "error": str(e), "results": self.cache.get("results", [])}
        finally:
            self._scanning = False

    # ─────────────────────────────────────────
    #  Cache-only read
    # ─────────────────────────────────────────
    def get_cached_results(self, limit: int = 20) -> dict:
        res = self.cache.get("results", [])
        return {
            "results": res[:limit],
            "last_scan": self.cache.get("last_scan"),
            "stats": self.cache.get("stats", {}),
            "is_scanning": self._scanning,
        }

    # ─────────────────────────────────────────
    #  Background auto-scan (non-bloquant)
    # ─────────────────────────────────────────
    def scan_async(self, force: bool = False) -> dict:
        """Lance le scan en thread sans bloquer la requête HTTP."""
        if self._scanning:
            return {"status": "already_running"}

        def _run():
            try:
                self.scan(force=force)
            except Exception as e:
                print(f"[ETFScanner] async scan error: {e}")

        self._bg_thread = threading.Thread(target=_run, daemon=True)
        self._bg_thread.start()
        return {"status": "started"}


# Singleton global
_ETF_SCANNER: Optional[ETFScanner] = None


def get_etf_scanner() -> ETFScanner:
    global _ETF_SCANNER
    if _ETF_SCANNER is None:
        _ETF_SCANNER = ETFScanner()
    return _ETF_SCANNER
