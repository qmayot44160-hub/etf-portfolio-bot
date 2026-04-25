"""
Module de récupération des données de marché via yfinance.

Un cache TTL (60s par défaut) est appliqué sur ``get_current_prices`` pour
éviter les appels redondants à yfinance pendant une même requête Flask
(``/api/portfolio``, ``/api/rebalance``, ``/api/dca`` enchaînent jusqu'à
3 appels identiques). Passer ``force=True`` ou appeler
``invalidate_price_cache()`` contourne le cache.
"""

import time
import threading
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from config import PORTFOLIO, BACKTEST_YEARS


# ─────────────────────────────────────────────────────────────
# Cache prix courant - partagé entre threads (Flask = multi-req)
# ─────────────────────────────────────────────────────────────
PRICE_CACHE_TTL_SEC = 60
_price_cache: dict = {}
_price_cache_ts: float = 0.0
_price_cache_lock = threading.Lock()


def invalidate_price_cache() -> None:
    """Force le prochain ``get_current_prices`` à refetcher (après un trade, un refresh manuel, etc.)."""
    global _price_cache, _price_cache_ts
    with _price_cache_lock:
        _price_cache = {}
        _price_cache_ts = 0.0


def _fetch_prices_from_yf() -> dict:
    """Appel yfinance réel - extrait ici pour la lisibilité + testabilité."""
    prices = {}
    tickers = list(PORTFOLIO.keys())
    data = yf.download(tickers, period="5d", progress=False)

    if "Close" in data.columns or hasattr(data.columns, "levels"):
        close = data["Close"]
    else:
        close = data

    for ticker in tickers:
        try:
            if isinstance(close, pd.DataFrame) and ticker in close.columns:
                series = close[ticker].dropna()
            elif isinstance(close, pd.Series):
                series = close.dropna()
            else:
                series = pd.Series(dtype=float)

            price = float(series.iloc[-1]) if len(series) > 0 else None
        except Exception:
            price = None
        prices[ticker] = round(price, 2) if price is not None else None

    return prices


def get_current_prices(force: bool = False) -> dict:
    """Récupère les prix actuels de tous les ETFs du portefeuille.

    Résultats cachés ``PRICE_CACHE_TTL_SEC`` secondes. Passer ``force=True``
    pour bypasser le cache (rare : l'UI appelle ``invalidate_price_cache()``
    après un trade pour la même chose).
    """
    global _price_cache, _price_cache_ts
    now = time.time()
    if not force and _price_cache and (now - _price_cache_ts) < PRICE_CACHE_TTL_SEC:
        return dict(_price_cache)  # copie défensive

    with _price_cache_lock:
        # Double-check - un autre thread a peut-être rafraîchi pendant qu'on attendait le lock
        if not force and _price_cache and (time.time() - _price_cache_ts) < PRICE_CACHE_TTL_SEC:
            return dict(_price_cache)
        prices = _fetch_prices_from_yf()
        _price_cache = prices
        _price_cache_ts = time.time()
        return dict(prices)


def get_historical_data(period_years: int = None) -> pd.DataFrame:
    """Récupère l'historique des prix pour le backtest."""
    if period_years is None:
        period_years = BACKTEST_YEARS

    tickers = list(PORTFOLIO.keys())
    end = datetime.now()
    start = end - timedelta(days=period_years * 365)

    data = yf.download(tickers, start=start, end=end, progress=False)

    if "Close" in data.columns or hasattr(data.columns, "levels"):
        close = data["Close"]
    else:
        close = data

    return close.dropna()


def get_etf_info(ticker: str) -> dict:
    """Récupère les infos détaillées d'un ETF."""
    etf = yf.Ticker(ticker)
    info = etf.info
    return {
        "name": info.get("longName", info.get("shortName", ticker)),
        "currency": info.get("currency", "N/A"),
        "expense_ratio": info.get("annualReportExpenseRatio", "N/A"),
        "total_assets": info.get("totalAssets", "N/A"),
        "ytd_return": info.get("ytdReturn", "N/A"),
    }
