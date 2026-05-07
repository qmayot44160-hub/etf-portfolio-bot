"""
Moteur de stratégie de trading - génère des signaux BUY/SELL/HOLD.

Stratégie multi-indicateurs :
- Tendance : EMA 9/21 crossover + SMA 50
- Momentum : RSI + MACD
- Volatilité : Bollinger Bands + ATR pour SL/TP
- Confirmation : Stochastic
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import pandas as pd
import numpy as np
from technical_analysis import compute_all_indicators, rsi_divergence


class Signal(Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


@dataclass
class TradeSignal:
    symbol: str
    signal: Signal
    price: float
    stop_loss: float
    take_profit: float
    confidence: float  # 0-100
    reasons: list
    indicators: dict
    timestamp: str = ""


class TradingStrategy:
    """
    Stratégie de trading basée sur plusieurs indicateurs.
    Chaque indicateur vote BUY/SELL/HOLD, le score total détermine le signal.
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        # RSI
        self.rsi_oversold = self.config.get("rsi_oversold", 30)
        self.rsi_overbought = self.config.get("rsi_overbought", 70)
        # SL/TP en multiples d'ATR
        self.sl_atr_mult = self.config.get("sl_atr_multiplier", 1.5)
        self.tp_atr_mult = self.config.get("tp_atr_multiplier", 3.0)
        # Risk par trade (% du portefeuille)
        self.risk_per_trade = self.config.get("risk_per_trade", 2.0)

    def analyze(self, df: pd.DataFrame, symbol: str) -> TradeSignal:
        """
        Analyse un DataFrame OHLCV et retourne un signal de trading.
        """
        df = compute_all_indicators(df.copy())
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest

        score = 0  # -5 à +5
        reasons = []

        # ── 1. Tendance EMA crossover (+/-1) ──
        if latest["ema_9"] > latest["ema_21"]:
            if prev["ema_9"] <= prev["ema_21"]:
                score += 1.5
                reasons.append("EMA 9 croise au-dessus de EMA 21 (bullish cross)")
            else:
                score += 0.5
                reasons.append("EMA 9 > EMA 21 (tendance haussiere)")
        else:
            if prev["ema_9"] >= prev["ema_21"]:
                score -= 1.5
                reasons.append("EMA 9 croise en-dessous de EMA 21 (bearish cross)")
            else:
                score -= 0.5
                reasons.append("EMA 9 < EMA 21 (tendance baissiere)")

        # ── 2. Prix vs SMA 50 (+/-0.5) ──
        if latest["close"] > latest["sma_50"]:
            score += 0.5
            reasons.append(f"Prix au-dessus de SMA 50 ({latest['sma_50']:.2f})")
        else:
            score -= 0.5
            reasons.append(f"Prix en-dessous de SMA 50 ({latest['sma_50']:.2f})")

        # ── 3. RSI (+/-1) ──
        rsi_val = latest["rsi"]
        if rsi_val < self.rsi_oversold:
            score += 1
            reasons.append(f"RSI survendu ({rsi_val:.1f} < {self.rsi_oversold})")
        elif rsi_val > self.rsi_overbought:
            score -= 1
            reasons.append(f"RSI suracheté ({rsi_val:.1f} > {self.rsi_overbought})")
        else:
            reasons.append(f"RSI neutre ({rsi_val:.1f})")

        # ── 4. MACD (+/-1) ──
        if latest["macd"] > latest["macd_signal"]:
            if prev["macd"] <= prev["macd_signal"]:
                score += 1
                reasons.append("MACD croise au-dessus du signal (achat)")
            else:
                score += 0.5
                reasons.append("MACD au-dessus du signal")
        else:
            if prev["macd"] >= prev["macd_signal"]:
                score -= 1
                reasons.append("MACD croise en-dessous du signal (vente)")
            else:
                score -= 0.5
                reasons.append("MACD en-dessous du signal")

        # ── 5. Bollinger Bands (+/-1) ──
        if latest["close"] <= latest["bb_lower"]:
            score += 1
            reasons.append("Prix touche la bande inferieure de Bollinger (survente)")
        elif latest["close"] >= latest["bb_upper"]:
            score -= 1
            reasons.append("Prix touche la bande superieure de Bollinger (surachat)")
        else:
            reasons.append("Prix dans les bandes de Bollinger")

        # ── 6. Stochastic (+/-0.5) ──
        if latest["stoch_k"] < 20 and latest["stoch_d"] < 20:
            score += 0.5
            reasons.append(f"Stochastic survendu (K={latest['stoch_k']:.1f})")
        elif latest["stoch_k"] > 80 and latest["stoch_d"] > 80:
            score -= 0.5
            reasons.append(f"Stochastic suracheté (K={latest['stoch_k']:.1f})")

        # ── 7. EMA 200 - filtre tendance long terme (+/-0.75) ──
        ema200 = latest.get("ema_200")
        if pd.notna(ema200):
            if latest["close"] > ema200:
                score += 0.75
                reasons.append(f"Prix au-dessus de EMA 200 (tendance long terme haussière)")
            else:
                score -= 0.75
                reasons.append(f"Prix en-dessous de EMA 200 (tendance long terme baissière)")

        # ── 8. Volume (OBV + CMF) (+/-0.75) ──
        cmf_val = latest.get("cmf", 0) or 0
        obv_now = latest.get("obv", 0) or 0
        obv_prev = df["obv"].iloc[-5] if len(df) >= 5 and "obv" in df.columns else obv_now
        obv_rising = obv_now > obv_prev
        if cmf_val > 0.05 and obv_rising:
            score += 0.75
            reasons.append(f"Volume acheteur confirmé (CMF={cmf_val:.3f}, OBV en hausse)")
        elif cmf_val < -0.05 and not obv_rising:
            score -= 0.75
            reasons.append(f"Volume vendeur dominant (CMF={cmf_val:.3f}, OBV en baisse)")
        elif abs(cmf_val) > 0.05:
            partial = 0.35 if cmf_val > 0 else -0.35
            score += partial
            reasons.append(f"Pression volume {'acheteuse' if cmf_val > 0 else 'vendeuse'} (CMF={cmf_val:.3f})")

        # ── 9. Divergence RSI (+/-1.5) - signal fort de retournement ──
        div = rsi_divergence(df["close"], df["rsi"], lookback=14)
        if div["bullish"]:
            score += 1.5 * (0.5 + div["strength"])
            reasons.append(f"Divergence RSI haussière détectée (force={div['strength']:.2f}) - signal de retournement")
        elif div["bearish"]:
            score -= 1.5 * (0.5 + div["strength"])
            reasons.append(f"Divergence RSI baissière détectée (force={div['strength']:.2f}) - signal de retournement")

        # ── Calcul du signal final ──
        # Score max théorique : ~8.5 (avec toutes les divergences)
        if score >= 3.5:
            signal = Signal.STRONG_BUY
        elif score >= 1.5:
            signal = Signal.BUY
        elif score <= -3.5:
            signal = Signal.STRONG_SELL
        elif score <= -1.5:
            signal = Signal.SELL
        else:
            signal = Signal.HOLD

        # ── Stop Loss & Take Profit basés sur ATR ──
        # SL/TP dynamique : plus serré en tendance forte, plus large en volatilité
        atr_val = latest["atr"] if pd.notna(latest["atr"]) else latest["close"] * 0.02
        price = latest["close"]

        # Ajustement SL selon l'alignement de tendance
        ema200_aligned = ema200 is not None and pd.notna(ema200) and (
            (signal in (Signal.BUY, Signal.STRONG_BUY) and price > ema200) or
            (signal in (Signal.SELL, Signal.STRONG_SELL) and price < ema200)
        )
        sl_mult = self.sl_atr_mult * (0.85 if ema200_aligned else 1.0)
        tp_mult = self.tp_atr_mult * (1.15 if ema200_aligned else 1.0)

        if signal in (Signal.BUY, Signal.STRONG_BUY):
            stop_loss = price - (atr_val * sl_mult)
            take_profit = price + (atr_val * tp_mult)
        elif signal in (Signal.SELL, Signal.STRONG_SELL):
            stop_loss = price + (atr_val * sl_mult)
            take_profit = price - (atr_val * tp_mult)
        else:
            stop_loss = price - (atr_val * self.sl_atr_mult)
            take_profit = price + (atr_val * self.tp_atr_mult)

        # Confiance sigmoidale : plus discriminante que linéaire
        # score / 8.5 normalise, puis sigmoide centrée sur 0
        normalized = abs(score) / 8.5
        confidence = round(100 / (1 + np.exp(-8 * (normalized - 0.35))), 1)
        confidence = min(confidence, 98.0)  # jamais 100%

        indicators = {
            "price": round(price, 4),
            "ema_9": round(latest["ema_9"], 4),
            "ema_21": round(latest["ema_21"], 4),
            "sma_50": round(latest["sma_50"], 4) if pd.notna(latest["sma_50"]) else None,
            "ema_200": round(ema200, 4) if ema200 is not None and pd.notna(ema200) else None,
            "rsi": round(rsi_val, 2),
            "macd": round(latest["macd"], 4),
            "macd_signal": round(latest["macd_signal"], 4),
            "bb_upper": round(latest["bb_upper"], 4),
            "bb_lower": round(latest["bb_lower"], 4),
            "atr": round(atr_val, 4),
            "stoch_k": round(latest["stoch_k"], 2) if pd.notna(latest["stoch_k"]) else None,
            "cmf": round(cmf_val, 4),
            "obv_trend": "up" if obv_rising else "down",
            "rsi_divergence": div,
            "ema200_aligned": ema200_aligned,
            "score": round(score, 2),
        }

        return TradeSignal(
            symbol=symbol,
            signal=signal,
            price=round(price, 4),
            stop_loss=round(stop_loss, 4),
            take_profit=round(take_profit, 4),
            confidence=round(confidence, 1),
            reasons=reasons,
            indicators=indicators,
        )
