"""
Modeles quantitatifs institutionnels.

- Detection de regime de marche (trending / mean-reverting / volatile / calm)
- Z-score mean-reversion
- Facteur momentum multi-horizon
- Volatilite realisee vs implicite
- Calcul d'edge statistique
- Indicateurs avances : Hurst exponent, ADX regime, volume profile
"""

import numpy as np
import pandas as pd
from enum import Enum
from dataclasses import dataclass
from typing import Optional


class MarketRegime(Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    MEAN_REVERTING = "MEAN_REVERTING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    BREAKOUT = "BREAKOUT"


@dataclass
class RegimeAnalysis:
    regime: MarketRegime
    confidence: float  # 0-100
    volatility_regime: str  # "low", "normal", "high", "extreme"
    trend_strength: float  # 0-100 (ADX-based)
    mean_reversion_score: float  # Z-score
    momentum_score: float
    hurst_exponent: float  # <0.5 = mean-reverting, >0.5 = trending
    details: dict


@dataclass
class QuantSignal:
    """Signal quantitatif combine."""
    symbol: str
    direction: str  # "LONG", "SHORT", "FLAT"
    edge: float  # expected edge in %
    confidence: float
    regime: MarketRegime
    strategies_agree: int
    strategies_total: int
    position_size_kelly: float  # Kelly fraction
    signals: dict  # signal par strategie
    risk_metrics: dict


class QuantModels:
    """Suite de modeles quantitatifs."""

    # ── Regime Detection ──

    @staticmethod
    def detect_regime(df: pd.DataFrame) -> RegimeAnalysis:
        """
        Detecte le regime de marche actuel.
        Combine : ADX, Hurst exponent, volatilite, structure de prix.
        """
        close = df["close"].values
        high = df["high"].values
        low = df["low"].values

        # 1. ADX - force de la tendance
        adx, plus_di, minus_di = QuantModels._adx(high, low, close, 14)
        trend_strength = adx[-1] if len(adx) > 0 else 0

        # 2. Hurst exponent - trending vs mean-reverting
        hurst = QuantModels._hurst_exponent(close)

        # 3. Volatilite realisee
        returns = np.diff(np.log(close))
        vol_20 = np.std(returns[-20:]) * np.sqrt(365) * 100 if len(returns) >= 20 else 0
        vol_60 = np.std(returns[-60:]) * np.sqrt(365) * 100 if len(returns) >= 60 else vol_20
        vol_ratio = vol_20 / vol_60 if vol_60 > 0 else 1.0

        # 4. Z-score (mean reversion)
        zscore = QuantModels._zscore(close, 20)

        # 5. Momentum multi-horizon
        mom = QuantModels._momentum_score(close)

        # Classification du regime de volatilite
        if vol_20 > 100:
            vol_regime = "extreme"
        elif vol_20 > 60:
            vol_regime = "high"
        elif vol_20 > 25:
            vol_regime = "normal"
        else:
            vol_regime = "low"

        # Classification du regime principal
        if trend_strength > 40 and hurst > 0.55:
            if mom > 0:
                regime = MarketRegime.TRENDING_UP
            else:
                regime = MarketRegime.TRENDING_DOWN
            confidence = min(trend_strength * 1.5, 100)
        elif hurst < 0.45 and trend_strength < 25:
            regime = MarketRegime.MEAN_REVERTING
            confidence = (0.5 - hurst) * 200
        elif vol_ratio > 1.5 and vol_regime in ("high", "extreme"):
            regime = MarketRegime.HIGH_VOLATILITY
            confidence = min(vol_ratio * 40, 100)
        elif vol_regime == "low" and trend_strength < 20:
            regime = MarketRegime.LOW_VOLATILITY
            confidence = min((1 - vol_ratio) * 100 + 30, 100)
        elif vol_ratio > 1.3 and trend_strength > 30:
            regime = MarketRegime.BREAKOUT
            confidence = min(trend_strength + vol_ratio * 20, 100)
        else:
            # Default basé sur la dominance
            if trend_strength > 25:
                regime = MarketRegime.TRENDING_UP if mom > 0 else MarketRegime.TRENDING_DOWN
            else:
                regime = MarketRegime.MEAN_REVERTING
            confidence = 40

        return RegimeAnalysis(
            regime=regime,
            confidence=round(confidence, 1),
            volatility_regime=vol_regime,
            trend_strength=round(trend_strength, 1),
            mean_reversion_score=round(zscore, 3),
            momentum_score=round(mom, 3),
            hurst_exponent=round(hurst, 3),
            details={
                "adx": round(adx[-1], 2) if len(adx) > 0 else 0,
                "plus_di": round(plus_di[-1], 2) if len(plus_di) > 0 else 0,
                "minus_di": round(minus_di[-1], 2) if len(minus_di) > 0 else 0,
                "vol_20d": round(vol_20, 2),
                "vol_60d": round(vol_60, 2),
                "vol_ratio": round(vol_ratio, 3),
            }
        )

    @staticmethod
    def _hurst_exponent(series, max_lag=20):
        """Calcule le Hurst exponent via R/S analysis."""
        ts = np.array(series, dtype=float)
        if len(ts) < max_lag * 2:
            return 0.5

        lags = range(2, min(max_lag + 1, len(ts) // 2))
        rs_values = []

        for lag in lags:
            segments = len(ts) // lag
            if segments < 1:
                continue
            rs_list = []
            for i in range(segments):
                segment = ts[i * lag:(i + 1) * lag]
                mean = np.mean(segment)
                deviations = segment - mean
                cumulative = np.cumsum(deviations)
                R = np.max(cumulative) - np.min(cumulative)
                S = np.std(segment, ddof=1) if np.std(segment, ddof=1) > 0 else 1e-10
                rs_list.append(R / S)
            if rs_list:
                rs_values.append((np.log(lag), np.log(np.mean(rs_list))))

        if len(rs_values) < 3:
            return 0.5

        x = np.array([v[0] for v in rs_values])
        y = np.array([v[1] for v in rs_values])

        try:
            slope = np.polyfit(x, y, 1)[0]
            return max(0.0, min(1.0, slope))
        except Exception:
            return 0.5

    @staticmethod
    def _adx(high, low, close, period=14):
        """Average Directional Index."""
        n = len(close)
        if n < period + 1:
            return np.array([25.0]), np.array([0.0]), np.array([0.0])

        tr = np.zeros(n)
        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)

        for i in range(1, n):
            h_l = high[i] - low[i]
            h_pc = abs(high[i] - close[i - 1])
            l_pc = abs(low[i] - close[i - 1])
            tr[i] = max(h_l, h_pc, l_pc)

            up = high[i] - high[i - 1]
            down = low[i - 1] - low[i]
            plus_dm[i] = up if up > down and up > 0 else 0
            minus_dm[i] = down if down > up and down > 0 else 0

        # Smoothed
        atr = np.zeros(n)
        s_plus = np.zeros(n)
        s_minus = np.zeros(n)

        atr[period] = np.mean(tr[1:period + 1])
        s_plus[period] = np.mean(plus_dm[1:period + 1])
        s_minus[period] = np.mean(minus_dm[1:period + 1])

        for i in range(period + 1, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
            s_plus[i] = (s_plus[i - 1] * (period - 1) + plus_dm[i]) / period
            s_minus[i] = (s_minus[i - 1] * (period - 1) + minus_dm[i]) / period

        with np.errstate(divide='ignore', invalid='ignore'):
            plus_di = np.where(atr > 0, 100 * s_plus / atr, 0)
            minus_di = np.where(atr > 0, 100 * s_minus / atr, 0)
            di_sum = plus_di + minus_di
            dx = np.where(di_sum > 0, 100 * np.abs(plus_di - minus_di) / di_sum, 0)
        plus_di = np.nan_to_num(plus_di, 0)
        minus_di = np.nan_to_num(minus_di, 0)
        dx = np.nan_to_num(dx, 0)

        adx = np.zeros(n)
        start = period * 2
        if start < n:
            adx[start] = np.mean(dx[period:start + 1])
            for i in range(start + 1, n):
                adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

        return adx, plus_di, minus_di

    @staticmethod
    def _zscore(series, lookback=20):
        """Z-score du prix par rapport a sa moyenne mobile."""
        if len(series) < lookback:
            return 0
        window = series[-lookback:]
        mean = np.mean(window)
        std = np.std(window)
        if std == 0:
            return 0
        return (series[-1] - mean) / std

    @staticmethod
    def _momentum_score(close):
        """Score momentum multi-horizon normalise."""
        if len(close) < 60:
            return 0

        # Momentum sur differents horizons
        mom_5 = (close[-1] / close[-5] - 1) * 100 if len(close) >= 5 else 0
        mom_10 = (close[-1] / close[-10] - 1) * 100 if len(close) >= 10 else 0
        mom_20 = (close[-1] / close[-20] - 1) * 100 if len(close) >= 20 else 0
        mom_60 = (close[-1] / close[-60] - 1) * 100 if len(close) >= 60 else 0

        # Score pondere (court terme a plus de poids)
        score = mom_5 * 0.4 + mom_10 * 0.3 + mom_20 * 0.2 + mom_60 * 0.1
        return score

    # ── Strategies Quantitatives ──

    @staticmethod
    def mean_reversion_signal(df: pd.DataFrame, lookback=20, entry_z=2.0, exit_z=0.5):
        """
        Strategie mean-reversion : entre quand le prix s'ecarte de la moyenne,
        sort quand il revient.
        """
        close = df["close"].values
        if len(close) < lookback:
            return {"signal": 0, "zscore": 0, "band_width": 0}

        zscore = QuantModels._zscore(close, lookback)
        mean = np.mean(close[-lookback:])
        std = np.std(close[-lookback:])
        band_width = (std / mean * 100) if mean > 0 else 0

        if zscore <= -entry_z:
            signal = 1  # Long (prix tres bas)
            strength = min(abs(zscore) / 3, 1.0)
        elif zscore >= entry_z:
            signal = -1  # Short (prix tres haut)
            strength = min(abs(zscore) / 3, 1.0)
        elif abs(zscore) <= exit_z:
            signal = 0  # Zone neutre
            strength = 0
        else:
            signal = 0
            strength = 0

        return {
            "signal": signal,
            "strength": round(strength, 3),
            "zscore": round(zscore, 3),
            "mean": round(mean, 4),
            "upper_band": round(mean + entry_z * std, 4),
            "lower_band": round(mean - entry_z * std, 4),
            "band_width": round(band_width, 2),
        }

    @staticmethod
    def momentum_signal(df: pd.DataFrame):
        """
        Strategie momentum : suit la tendance avec confirmation de volume.
        """
        close = df["close"].values
        volume = df["volume"].values if "volume" in df.columns else np.ones(len(close))

        if len(close) < 60:
            return {"signal": 0, "score": 0}

        # Momentum ranks
        mom_5 = close[-1] / close[-5] - 1
        mom_20 = close[-1] / close[-20] - 1
        mom_60 = close[-1] / close[-60] - 1

        # Volume confirmation
        avg_vol_20 = np.mean(volume[-20:])
        recent_vol = np.mean(volume[-5:])
        vol_surge = recent_vol / avg_vol_20 if avg_vol_20 > 0 else 1

        # Rate of change acceleration
        roc_5 = mom_5
        prev_roc_5 = (close[-5] / close[-10] - 1) if len(close) >= 10 else 0
        roc_accel = roc_5 - prev_roc_5

        # Score composite
        score = mom_5 * 0.3 + mom_20 * 0.4 + mom_60 * 0.3
        score *= min(vol_surge, 2.0)  # Volume boost (cap a 2x)

        if score > 0.02 and roc_accel > 0:
            signal = 1
        elif score < -0.02 and roc_accel < 0:
            signal = -1
        else:
            signal = 0

        return {
            "signal": signal,
            "score": round(score * 100, 3),
            "momentum_5d": round(mom_5 * 100, 2),
            "momentum_20d": round(mom_20 * 100, 2),
            "momentum_60d": round(mom_60 * 100, 2),
            "volume_surge": round(vol_surge, 2),
            "roc_acceleration": round(roc_accel * 100, 3),
        }

    @staticmethod
    def breakout_signal(df: pd.DataFrame, lookback=20):
        """
        Strategie breakout : detecte les sorties de range avec volume.
        """
        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        volume = df["volume"].values if "volume" in df.columns else np.ones(len(close))

        if len(close) < lookback + 5:
            return {"signal": 0}

        # Range sur la periode lookback (excluant les 2 dernières bougies)
        range_high = np.max(high[-lookback - 2:-2])
        range_low = np.min(low[-lookback - 2:-2])
        range_width = (range_high - range_low) / range_low * 100 if range_low > 0 else 0

        # Volume moyen et actuel
        avg_vol = np.mean(volume[-lookback:])
        curr_vol = np.mean(volume[-3:])
        vol_ratio = curr_vol / avg_vol if avg_vol > 0 else 1

        price = close[-1]

        # ATR pour mesurer la force du breakout
        atr_vals = []
        for i in range(-14, 0):
            if len(close) + i > 0:
                tr = max(
                    high[i] - low[i],
                    abs(high[i] - close[i - 1]),
                    abs(low[i] - close[i - 1])
                )
                atr_vals.append(tr)
        atr = np.mean(atr_vals) if atr_vals else 0

        breakout_strength = 0
        if price > range_high:
            signal = 1
            breakout_strength = (price - range_high) / atr if atr > 0 else 0
        elif price < range_low:
            signal = -1
            breakout_strength = (range_low - price) / atr if atr > 0 else 0
        else:
            signal = 0

        # Le volume doit confirmer le breakout
        if signal != 0 and vol_ratio < 1.2:
            signal = 0  # Pas de confirmation volume → faux breakout probable

        return {
            "signal": signal,
            "breakout_strength": round(breakout_strength, 3),
            "range_high": round(range_high, 4),
            "range_low": round(range_low, 4),
            "range_width_pct": round(range_width, 2),
            "volume_ratio": round(vol_ratio, 2),
        }

    @staticmethod
    def volatility_signal(df: pd.DataFrame):
        """
        Strategie volatilite : trade les contractions/expansions de vol.
        """
        close = df["close"].values
        if len(close) < 60:
            return {"signal": 0}

        returns = np.diff(np.log(close))

        vol_10 = np.std(returns[-10:]) * np.sqrt(365) * 100
        vol_20 = np.std(returns[-20:]) * np.sqrt(365) * 100
        vol_60 = np.std(returns[-60:]) * np.sqrt(365) * 100

        # Bollinger Band width (proxy de vol)
        sma20 = np.mean(close[-20:])
        std20 = np.std(close[-20:])
        bb_width = (2 * std20 / sma20 * 100) if sma20 > 0 else 0

        # BB width percentile (historique)
        bb_widths = []
        for i in range(60, len(close)):
            m = np.mean(close[i - 20:i])
            s = np.std(close[i - 20:i])
            if m > 0:
                bb_widths.append(2 * s / m * 100)
        bb_percentile = 0
        if bb_widths:
            bb_percentile = sum(1 for w in bb_widths if w < bb_width) / len(bb_widths) * 100

        # Squeeze detection (BB inside Keltner)
        squeeze = bb_percentile < 20  # Vol tres basse → squeeze imminent

        # Vol regime shift
        vol_expanding = vol_10 > vol_20 * 1.3
        vol_contracting = vol_10 < vol_20 * 0.7

        signal = 0
        if squeeze:
            signal = 0  # Attendre le breakout
        elif vol_expanding and vol_10 > vol_60:
            # Forte expansion → suivre la direction
            if close[-1] > close[-5]:
                signal = 1
            else:
                signal = -1

        return {
            "signal": signal,
            "vol_10d": round(vol_10, 2),
            "vol_20d": round(vol_20, 2),
            "vol_60d": round(vol_60, 2),
            "bb_width": round(bb_width, 2),
            "bb_percentile": round(bb_percentile, 1),
            "squeeze": squeeze,
            "vol_expanding": vol_expanding,
            "vol_contracting": vol_contracting,
        }

    @staticmethod
    def orderflow_signal(df: pd.DataFrame):
        """
        Analyse du flux d'ordres via les bougies (sans carnet d'ordres).
        - Volume delta (estimation via close position dans la bougie)
        - Absorption detection
        - Imbalance
        """
        close = df["close"].values
        open_ = df["open"].values
        high = df["high"].values
        low = df["low"].values
        volume = df["volume"].values if "volume" in df.columns else np.ones(len(close))

        if len(close) < 20:
            return {"signal": 0}

        # Volume delta estimation
        # Si close > open → volume positif (acheteur)
        # Ponderation par position du close dans le range
        deltas = []
        for i in range(-20, 0):
            rng = high[i] - low[i]
            if rng > 0:
                close_pos = (close[i] - low[i]) / rng  # 0 = bottom, 1 = top
                delta = volume[i] * (2 * close_pos - 1)  # -1 to +1
            else:
                delta = 0
            deltas.append(delta)

        cum_delta = np.cumsum(deltas)
        delta_trend = cum_delta[-1] - cum_delta[0]

        # Absorption : gros volume mais petit mouvement
        recent_vols = volume[-5:]
        recent_ranges = [(high[i] - low[i]) for i in range(-5, 0)]
        avg_vol = np.mean(volume[-20:])
        avg_range = np.mean([(high[i] - low[i]) for i in range(-20, 0)])

        absorption = False
        if np.mean(recent_vols) > avg_vol * 1.5 and np.mean(recent_ranges) < avg_range * 0.7:
            absorption = True

        # Imbalance
        buy_vol = sum(d for d in deltas[-10:] if d > 0)
        sell_vol = abs(sum(d for d in deltas[-10:] if d < 0))
        total = buy_vol + sell_vol
        imbalance = (buy_vol - sell_vol) / total if total > 0 else 0

        signal = 0
        if imbalance > 0.3 and delta_trend > 0:
            signal = 1
        elif imbalance < -0.3 and delta_trend < 0:
            signal = -1

        if absorption:
            signal = -signal  # L'absorption inverse le signal (les gros acteurs absorbent)

        return {
            "signal": signal,
            "cumulative_delta": round(delta_trend, 2),
            "imbalance": round(imbalance, 3),
            "absorption_detected": absorption,
            "buy_volume_pct": round(buy_vol / total * 100, 1) if total > 0 else 50,
        }

    # ── Combinaison Multi-Strategie ──

    @staticmethod
    def _kelly_from_history(trade_history: list) -> Optional[dict]:
        """
        Calcule win_rate et ratio gain/perte depuis l'historique reel des trades.
        Retourne None si moins de 5 trades fermes.
        """
        closed = [
            t for t in trade_history
            if t.get("status") in ("TP_HIT", "SL_HIT", "TRAILING_SL", "MANUAL_CLOSE")
            and t.get("pnl") is not None
        ]
        if len(closed) < 5:
            return None
        wins = [t["pnl"] for t in closed if t["pnl"] > 0]
        losses = [abs(t["pnl"]) for t in closed if t["pnl"] <= 0]
        win_rate = len(wins) / len(closed)
        avg_win = float(np.mean(wins)) if wins else 0.0
        avg_loss = float(np.mean(losses)) if losses else 1.0
        return {
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "sample_size": len(closed),
        }

    @staticmethod
    def generate_quant_signal(df: pd.DataFrame, symbol: str,
                              trade_history: list = None) -> QuantSignal:
        """
        Genere un signal quantitatif combine a partir de toutes les strategies.
        Pondere par le regime de marche.
        """
        # 1. Detect regime
        regime = QuantModels.detect_regime(df)

        # 2. Run all strategies
        mr = QuantModels.mean_reversion_signal(df)
        mom = QuantModels.momentum_signal(df)
        bo = QuantModels.breakout_signal(df)
        vol = QuantModels.volatility_signal(df)
        of = QuantModels.orderflow_signal(df)

        # 3. Weight strategies by regime
        weights = QuantModels._get_regime_weights(regime.regime)

        signals = {
            "mean_reversion": {"signal": mr["signal"], "weight": weights["mean_reversion"], "data": mr},
            "momentum": {"signal": mom["signal"], "weight": weights["momentum"], "data": mom},
            "breakout": {"signal": bo["signal"], "weight": weights["breakout"], "data": bo},
            "volatility": {"signal": vol["signal"], "weight": weights["volatility"], "data": vol},
            "orderflow": {"signal": of["signal"], "weight": weights["orderflow"], "data": of},
        }

        # 4. Weighted vote
        total_weight = sum(s["weight"] for s in signals.values())
        weighted_signal = sum(
            s["signal"] * s["weight"] for s in signals.values()
        )
        normalized = weighted_signal / total_weight if total_weight > 0 else 0

        # Count agreements
        long_count = sum(1 for s in signals.values() if s["signal"] > 0)
        short_count = sum(1 for s in signals.values() if s["signal"] < 0)
        total_strats = sum(1 for s in signals.values() if s["signal"] != 0)

        if normalized > 0.3:
            direction = "LONG"
            agree = long_count
        elif normalized < -0.3:
            direction = "SHORT"
            agree = short_count
        else:
            direction = "FLAT"
            agree = 0

        # 5. Confidence
        confidence = abs(normalized) * 100
        if total_strats > 0:
            agreement_bonus = (agree / len(signals)) * 30
            confidence = min(confidence + agreement_bonus, 100)
        confidence = round(confidence * regime.confidence / 100, 1)

        # 6. Edge estimation (simplified)
        close = df["close"].values
        returns = np.diff(np.log(close))
        avg_return = np.mean(returns[-20:]) * 100
        vol = np.std(returns[-20:]) * 100
        edge = avg_return * (1 if direction == "LONG" else -1 if direction == "SHORT" else 0)

        # 7. Kelly criterion - utilise l'historique reel si disponible
        history_stats = None
        if trade_history:
            history_stats = QuantModels._kelly_from_history(trade_history)

        if direction != "FLAT":
            if history_stats:
                # Kelly base sur stats reelles
                win_rate = history_stats["win_rate"]
                price_ref = close[-1] if close[-1] > 0 else 1.0
                avg_win_pct = history_stats["avg_win"] / price_ref * 100
                avg_loss_pct = history_stats["avg_loss"] / price_ref * 100
                if avg_win_pct > 0:
                    kelly = (win_rate * avg_win_pct - (1 - win_rate) * avg_loss_pct) / avg_win_pct
                else:
                    kelly = 0
            elif vol > 0:
                # Fallback synthetique
                win_rate = max(0.3, min(0.7, 0.5 + edge / (vol * 2)))
                avg_win = vol * 1.5
                avg_loss = vol
                kelly = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win if avg_win > 0 else 0
            else:
                kelly = 0
                win_rate = 0.5
            kelly = max(0, min(0.25, kelly))  # Cap a 25% du capital
        else:
            kelly = 0
            win_rate = history_stats["win_rate"] if history_stats else 0.5

        return QuantSignal(
            symbol=symbol,
            direction=direction,
            edge=round(edge, 3),
            confidence=confidence,
            regime=regime.regime,
            strategies_agree=agree,
            strategies_total=len(signals),
            position_size_kelly=round(kelly, 4),
            signals=signals,
            risk_metrics={
                "volatility_20d": round(vol * np.sqrt(365), 2),
                "estimated_win_rate": round(win_rate * 100, 1),
                "kelly_source": "real_history" if history_stats else "synthetic",
                "kelly_sample_size": history_stats["sample_size"] if history_stats else 0,
                "regime": regime.regime.value,
                "regime_confidence": regime.confidence,
                "hurst": regime.hurst_exponent,
                "trend_strength": regime.trend_strength,
            }
        )

    @staticmethod
    def _get_regime_weights(regime: MarketRegime) -> dict:
        """
        Poids des strategies selon le regime.
        Les banques adaptent toujours leur mix au contexte.
        """
        base = {
            "mean_reversion": 0.2,
            "momentum": 0.2,
            "breakout": 0.2,
            "volatility": 0.2,
            "orderflow": 0.2,
        }

        overrides = {
            MarketRegime.TRENDING_UP: {"momentum": 0.35, "breakout": 0.25, "mean_reversion": 0.05, "orderflow": 0.20, "volatility": 0.15},
            MarketRegime.TRENDING_DOWN: {"momentum": 0.35, "breakout": 0.25, "mean_reversion": 0.05, "orderflow": 0.20, "volatility": 0.15},
            MarketRegime.MEAN_REVERTING: {"mean_reversion": 0.40, "orderflow": 0.25, "volatility": 0.15, "momentum": 0.10, "breakout": 0.10},
            MarketRegime.HIGH_VOLATILITY: {"volatility": 0.30, "orderflow": 0.25, "mean_reversion": 0.20, "momentum": 0.15, "breakout": 0.10},
            MarketRegime.LOW_VOLATILITY: {"breakout": 0.35, "volatility": 0.25, "mean_reversion": 0.15, "momentum": 0.15, "orderflow": 0.10},
            MarketRegime.BREAKOUT: {"breakout": 0.35, "momentum": 0.30, "volatility": 0.15, "orderflow": 0.15, "mean_reversion": 0.05},
        }
        return overrides.get(regime, base)
