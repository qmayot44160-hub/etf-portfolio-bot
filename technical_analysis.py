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


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """
    On-Balance Volume - confirme la tendance par le volume.
    Hausse sur volume fort = accumulation institutionnelle.
    """
    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (direction * volume).cumsum()


def cmf(high: pd.Series, low: pd.Series, close: pd.Series,
        volume: pd.Series, period: int = 20) -> pd.Series:
    """
    Chaikin Money Flow - flux d'argent sur une période.
    > 0.05 : pression acheteuse. < -0.05 : pression vendeuse.
    """
    clv = ((close - low) - (high - close)) / (high - low).replace(0, np.nan)
    clv = clv.fillna(0)
    money_flow_vol = clv * volume
    return money_flow_vol.rolling(period).sum() / volume.rolling(period).sum()


def rsi_divergence(close: pd.Series, rsi_series: pd.Series,
                   lookback: int = 14) -> dict:
    """
    Detecete les divergences RSI/prix.
    - Divergence haussiere : prix fait lower low, RSI fait higher low
    - Divergence baissiere : prix fait higher high, RSI fait lower high

    Retourne {'bullish': bool, 'bearish': bool, 'strength': 0-1}
    """
    if len(close) < lookback * 2:
        return {"bullish": False, "bearish": False, "strength": 0.0}

    prices = close.values[-lookback * 2:]
    rsis = rsi_series.values[-lookback * 2:]
    half = lookback

    # Comparer premiere moitie vs seconde moitie
    price_first = prices[:half]
    price_second = prices[half:]
    rsi_first = rsis[:half]
    rsi_second = rsis[half:]

    price_min_1 = np.nanmin(price_first)
    price_min_2 = np.nanmin(price_second)
    rsi_min_1 = np.nanmin(rsi_first)
    rsi_min_2 = np.nanmin(rsi_second)

    price_max_1 = np.nanmax(price_first)
    price_max_2 = np.nanmax(price_second)
    rsi_max_1 = np.nanmax(rsi_first)
    rsi_max_2 = np.nanmax(rsi_second)

    # Divergence haussiere : prix baisse mais RSI monte
    bullish = (price_min_2 < price_min_1 * 0.995) and (rsi_min_2 > rsi_min_1 + 2)
    # Divergence baissiere : prix monte mais RSI baisse
    bearish = (price_max_2 > price_max_1 * 1.005) and (rsi_max_2 < rsi_max_1 - 2)

    # Force basee sur l'ecart RSI
    if bullish:
        strength = min((rsi_min_2 - rsi_min_1) / 20, 1.0)
    elif bearish:
        strength = min((rsi_max_1 - rsi_max_2) / 20, 1.0)
    else:
        strength = 0.0

    return {
        "bullish": bullish,
        "bearish": bearish,
        "strength": round(strength, 3),
    }


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


def squeeze_momentum(high: pd.Series, low: pd.Series, close: pd.Series,
                     bb_period: int = 20, bb_mult: float = 2.0,
                     kc_period: int = 20, kc_mult: float = 1.5) -> dict:
    """
    TTM Squeeze (John Carter) - compresse de volatilite avant explosion.
    Squeeze ON : BB entierement dans le Keltner Channel = energie accumulee.
    Squeeze OFF (release) : BB sort du KC = signal directionnel imminent.
    Momentum : position du close par rapport au milieu du range, lisse.
    """
    # Bollinger Bands
    bb_mid = sma(close, bb_period)
    bb_std = close.rolling(window=bb_period).std()
    bb_upper = bb_mid + bb_mult * bb_std
    bb_lower = bb_mid - bb_mult * bb_std

    # Keltner Channel (base EMA + ATR)
    kc_mid = ema(close, kc_period)
    kc_atr = atr(high, low, close, kc_period)
    kc_upper = kc_mid + kc_mult * kc_atr
    kc_lower = kc_mid - kc_mult * kc_atr

    # Squeeze : BB entierement a l'interieur du KC
    squeeze_on = (bb_upper < kc_upper) & (bb_lower > kc_lower)

    # Momentum oscillateur : delta entre close et midpoint (HL + BB)
    highest = high.rolling(window=kc_period).max()
    lowest = low.rolling(window=kc_period).min()
    mid_hl = (highest + lowest) / 2
    val = close - (mid_hl + bb_mid) / 2
    # Moyenne glissante comme approximation de la regression lineaire
    momentum = val.rolling(window=kc_period).mean()

    return {
        "squeeze_on": squeeze_on,
        "momentum": momentum,
    }


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
    df["ema_200"] = ema(close, 200)  # Filtre long terme institutionnel

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

    # Volume indicators
    vol = df["volume"] if "volume" in df.columns else pd.Series(
        np.ones(len(df)), index=df.index)
    df["obv"] = obv(close, vol)
    df["cmf"] = cmf(high, low, close, vol, 20)

    # Squeeze Momentum TTM
    sq = squeeze_momentum(high, low, close)
    df["squeeze_on"] = sq["squeeze_on"]
    df["squeeze_momentum"] = sq["momentum"]

    return df
