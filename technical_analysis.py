"""
Module d'analyse technique - indicateurs pour le trading actif.
"""

import pandas as pd
import numpy as np


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (0-100)."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """MACD - Moving Average Convergence Divergence."""
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return {
        "macd": macd_line,
        "signal": signal_line,
        "histogram": histogram,
    }


def bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> dict:
    """Bandes de Bollinger."""
    middle = sma(series, period)
    std = series.rolling(window=period).std()
    return {
        "upper": middle + (std * std_dev),
        "middle": middle,
        "lower": middle - (std * std_dev),
    }


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range - mesure de volatilité."""
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
               k_period: int = 14, d_period: int = 3) -> dict:
    """Stochastic Oscillator (%K et %D)."""
    lowest_low = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()
    k = 100 * (close - lowest_low) / (highest_high - lowest_low)
    d = k.rolling(window=d_period).mean()
    return {"k": k, "d": d}


def support_resistance(close: pd.Series, window: int = 20) -> dict:
    """Détecte les niveaux de support et résistance."""
    rolling_min = close.rolling(window=window).min()
    rolling_max = close.rolling(window=window).max()
    return {
        "support": rolling_min.iloc[-1] if len(rolling_min) > 0 else None,
        "resistance": rolling_max.iloc[-1] if len(rolling_max) > 0 else None,
    }


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule tous les indicateurs sur un DataFrame avec colonnes OHLCV.
    Attend: open, high, low, close, volume
    """
    close = df["close"]
    high = df["high"]
    low = df["low"]

    # Moving Averages
    df["sma_20"] = sma(close, 20)
    df["sma_50"] = sma(close, 50)
    df["ema_9"] = ema(close, 9)
    df["ema_21"] = ema(close, 21)

    # RSI
    df["rsi"] = rsi(close, 14)

    # MACD
    m = macd(close)
    df["macd"] = m["macd"]
    df["macd_signal"] = m["signal"]
    df["macd_hist"] = m["histogram"]

    # Bollinger
    bb = bollinger_bands(close, 20)
    df["bb_upper"] = bb["upper"]
    df["bb_middle"] = bb["middle"]
    df["bb_lower"] = bb["lower"]

    # ATR
    df["atr"] = atr(high, low, close, 14)

    # Stochastic
    stoch = stochastic(high, low, close)
    df["stoch_k"] = stoch["k"]
    df["stoch_d"] = stoch["d"]

    return df
