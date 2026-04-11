"""
Module de récupération des données de marché via yfinance.
"""

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from config import PORTFOLIO, BACKTEST_YEARS


def get_current_prices() -> dict:
    """Récupère les prix actuels de tous les ETFs du portefeuille."""
    prices = {}
    tickers = list(PORTFOLIO.keys())
    data = yf.download(tickers, period="5d", progress=False)

    if "Close" in data.columns or hasattr(data.columns, "levels"):
        close = data["Close"]
    else:
        close = data

    for ticker in tickers:
        if isinstance(close, pd.DataFrame) and ticker in close.columns:
            price = close[ticker].dropna().iloc[-1]
        elif isinstance(close, pd.Series):
            price = close.dropna().iloc[-1]
        else:
            price = None
        prices[ticker] = round(float(price), 2) if price is not None else None

    return prices


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
