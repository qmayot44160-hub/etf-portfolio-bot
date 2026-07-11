"""
Intelligence de marche avancee.

- Funding rates & open interest (via MEXC)
- Liquidation heatmap estimation
- Whale detection (gros volumes)
- Sentiment via Fear & Greed Index
- Divergences volume/prix
- Support/Resistance dynamiques
- Market structure analysis (Higher Highs, Lower Lows)
"""

import numpy as np
import pandas as pd
from datetime import datetime
from dataclasses import dataclass
from typing import Optional


@dataclass
class MarketIntel:
    symbol: str
    sentiment: str  # "EXTREME_FEAR", "FEAR", "NEUTRAL", "GREED", "EXTREME_GREED"
    sentiment_score: float  # 0-100
    whale_activity: str  # "LOW", "MEDIUM", "HIGH"
    volume_trend: str  # "DECLINING", "STABLE", "INCREASING", "SURGE"
    market_structure: str  # "BULLISH", "BEARISH", "RANGING"
    support_levels: list
    resistance_levels: list
    key_levels: list  # Niveaux psychologiques importants
    divergences: list
    funding_rate: Optional[float]
    open_interest_change: Optional[float]
    details: dict
    market_fng: Optional[int] = None          # Fear & Greed global crypto (0-100)
    market_fng_label: Optional[str] = None     # libellé FR (Peur, Avidité…)


class MarketIntelligence:
    """Analyse approfondie du marche."""

    def __init__(self, exchange=None):
        self.exchange = exchange

    def full_analysis(self, df: pd.DataFrame, symbol: str) -> MarketIntel:
        """Analyse complete d'un symbole."""
        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        volume = df["volume"].values if "volume" in df.columns else np.ones(len(close))
        open_ = df["open"].values

        # Market structure
        structure = self._analyze_market_structure(high, low, close)

        # Support/Resistance
        supports, resistances = self._find_sr_levels(high, low, close, volume)

        # Volume analysis
        vol_trend, vol_details = self._analyze_volume(close, volume)

        # Whale detection
        whale = self._detect_whales(volume, close, open_, high, low)

        # Divergences
        divs = self._detect_divergences(close, volume)

        # Sentiment (basé sur indicateurs techniques, propre au symbole)
        sentiment_score, sentiment = self._calculate_sentiment(close, volume, high, low)

        # Sentiment GLOBAL crypto : Fear & Greed Index reel (IA n6)
        fng_val = None
        fng_label = None
        try:
            from sentiment_feed import get_fear_greed
            fng = get_fear_greed()
            if fng.get("available"):
                fng_val = fng["value"]
                fng_label = fng["label_fr"]
        except Exception:
            pass

        # Key psychological levels
        key_levels = self._find_key_levels(close[-1])

        # Funding & OI (si exchange disponible)
        funding = None
        oi_change = None
        if self.exchange:
            try:
                funding = self._get_funding_rate(symbol)
            except Exception:
                pass
            try:
                oi_change = self._get_oi_change(symbol)
            except Exception:
                pass

        return MarketIntel(
            symbol=symbol,
            sentiment=sentiment,
            sentiment_score=round(sentiment_score, 1),
            whale_activity=whale["level"],
            volume_trend=vol_trend,
            market_structure=structure["structure"],
            support_levels=[round(s, 4) for s in supports[:5]],
            resistance_levels=[round(r, 4) for r in resistances[:5]],
            key_levels=[round(k, 2) for k in key_levels[:5]],
            divergences=divs,
            funding_rate=funding,
            open_interest_change=oi_change,
            details={
                "structure": structure,
                "volume": vol_details,
                "whale": whale,
                "sr_method": "volume_profile + swing_points",
            },
            market_fng=fng_val,
            market_fng_label=fng_label,
        )

    # ── Market Structure ──

    def _analyze_market_structure(self, high, low, close) -> dict:
        """
        Analyse la structure de marché : HH/HL (bullish), LH/LL (bearish).
        """
        if len(close) < 20:
            return {"structure": "RANGING", "swings": []}

        # Trouver les swing highs et lows
        swing_highs = []
        swing_lows = []
        lookback = 5

        for i in range(lookback, len(high) - lookback):
            if high[i] == max(high[i - lookback:i + lookback + 1]):
                swing_highs.append((i, high[i]))
            if low[i] == min(low[i - lookback:i + lookback + 1]):
                swing_lows.append((i, low[i]))

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return {"structure": "RANGING", "swings": []}

        # Analyser les 4 derniers swings
        recent_highs = swing_highs[-3:]
        recent_lows = swing_lows[-3:]

        hh = all(recent_highs[i][1] > recent_highs[i - 1][1] for i in range(1, len(recent_highs)))
        hl = all(recent_lows[i][1] > recent_lows[i - 1][1] for i in range(1, len(recent_lows)))
        lh = all(recent_highs[i][1] < recent_highs[i - 1][1] for i in range(1, len(recent_highs)))
        ll = all(recent_lows[i][1] < recent_lows[i - 1][1] for i in range(1, len(recent_lows)))

        if hh and hl:
            structure = "BULLISH"
        elif lh and ll:
            structure = "BEARISH"
        else:
            structure = "RANGING"

        return {
            "structure": structure,
            "higher_highs": hh,
            "higher_lows": hl,
            "lower_highs": lh,
            "lower_lows": ll,
            "last_swing_high": round(recent_highs[-1][1], 4) if recent_highs else 0,
            "last_swing_low": round(recent_lows[-1][1], 4) if recent_lows else 0,
        }

    # ── Support / Resistance ──

    def _find_sr_levels(self, high, low, close, volume, num_levels=10) -> tuple:
        """
        Trouve les niveaux S/R par volume profile + swing points.
        """
        if len(close) < 20:
            return [], []

        current = close[-1]

        # Volume profile - zones de fort volume = S/R
        n_bins = 50
        price_range = np.linspace(np.min(low[-100:]), np.max(high[-100:]), n_bins)
        vol_profile = np.zeros(n_bins)

        period = min(100, len(close))
        for i in range(-period, 0):
            for j in range(n_bins):
                if low[i] <= price_range[j] <= high[i]:
                    vol_profile[j] += volume[i]

        # High Volume Nodes (HVN) = S/R potentiel
        threshold = np.percentile(vol_profile, 75)
        hvn_indices = np.where(vol_profile > threshold)[0]
        hvn_levels = price_range[hvn_indices]

        # Swing points
        swing_levels = []
        for i in range(3, len(close) - 3):
            if high[i] == max(high[i - 3:i + 4]):
                swing_levels.append(high[i])
            if low[i] == min(low[i - 3:i + 4]):
                swing_levels.append(low[i])

        # Combiner et trier
        all_levels = list(hvn_levels) + swing_levels
        if not all_levels:
            return [], []

        # Cluster les niveaux proches (dans 0.5%)
        clustered = self._cluster_levels(all_levels, tolerance_pct=0.5)

        supports = sorted([l for l in clustered if l < current], reverse=True)[:num_levels]
        resistances = sorted([l for l in clustered if l > current])[:num_levels]

        return supports, resistances

    @staticmethod
    def _cluster_levels(levels, tolerance_pct=0.5):
        """Regroupe les niveaux proches."""
        if not levels:
            return []
        sorted_levels = sorted(levels)
        clusters = [[sorted_levels[0]]]

        for level in sorted_levels[1:]:
            if abs(level - clusters[-1][-1]) / clusters[-1][-1] * 100 < tolerance_pct:
                clusters[-1].append(level)
            else:
                clusters.append([level])

        return [np.mean(c) for c in clusters]

    # ── Volume Analysis ──

    def _analyze_volume(self, close, volume) -> tuple:
        """Analyse les tendances de volume."""
        if len(volume) < 20:
            return "STABLE", {}

        avg_5 = np.mean(volume[-5:])
        avg_20 = np.mean(volume[-20:])
        avg_50 = np.mean(volume[-50:]) if len(volume) >= 50 else avg_20

        ratio_5_20 = avg_5 / avg_20 if avg_20 > 0 else 1

        if ratio_5_20 > 2.0:
            trend = "SURGE"
        elif ratio_5_20 > 1.3:
            trend = "INCREASING"
        elif ratio_5_20 < 0.7:
            trend = "DECLINING"
        else:
            trend = "STABLE"

        # Volume trend vs price trend
        price_up = close[-1] > close[-5] if len(close) >= 5 else True
        vol_up = avg_5 > avg_20

        if price_up and vol_up:
            confirmation = "STRONG_BULLISH"
        elif price_up and not vol_up:
            confirmation = "WEAK_BULLISH"
        elif not price_up and vol_up:
            confirmation = "STRONG_BEARISH"
        else:
            confirmation = "WEAK_BEARISH"

        details = {
            "avg_5d": round(avg_5, 2),
            "avg_20d": round(avg_20, 2),
            "avg_50d": round(avg_50, 2),
            "ratio_5_20": round(ratio_5_20, 2),
            "confirmation": confirmation,
        }

        return trend, details

    # ── Whale Detection ──

    def _detect_whales(self, volume, close, open_, high, low) -> dict:
        """
        Detecte l'activite des whales via des bougies anormales.
        """
        if len(volume) < 20:
            return {"level": "LOW", "signals": []}

        avg_vol = np.mean(volume[-50:]) if len(volume) >= 50 else np.mean(volume)
        std_vol = np.std(volume[-50:]) if len(volume) >= 50 else np.std(volume)

        signals = []
        whale_count = 0

        for i in range(-10, 0):
            vol = volume[i]
            body = abs(close[i] - open_[i])
            full_range = high[i] - low[i]

            # Volume spike (> 2 ecarts-types)
            if vol > avg_vol + 2 * std_vol:
                whale_count += 1
                # Gros volume + petit corps = absorption (whales)
                if full_range > 0 and body / full_range < 0.3:
                    signals.append({
                        "type": "ABSORPTION",
                        "bar_index": i,
                        "volume_ratio": round(vol / avg_vol, 1),
                    })
                # Gros volume + grand corps = conviction
                elif full_range > 0 and body / full_range > 0.7:
                    direction = "BUY" if close[i] > open_[i] else "SELL"
                    signals.append({
                        "type": f"WHALE_{direction}",
                        "bar_index": i,
                        "volume_ratio": round(vol / avg_vol, 1),
                    })

        if whale_count >= 3:
            level = "HIGH"
        elif whale_count >= 1:
            level = "MEDIUM"
        else:
            level = "LOW"

        return {
            "level": level,
            "whale_candles_10d": whale_count,
            "signals": signals[-5:],
        }

    # ── Divergences ──

    def _detect_divergences(self, close, volume) -> list:
        """
        Detecte les divergences prix/volume.
        Divergence = prix fait un nouveau high mais pas le volume (ou inverse).
        """
        if len(close) < 30:
            return []

        divergences = []

        # Trouver les 2 derniers swing highs
        for lookback in [5, 10, 20]:
            if len(close) < lookback * 3:
                continue

            # Comparer le dernier swing avec le precedent
            recent = close[-lookback:]
            prev = close[-lookback * 2:-lookback]
            recent_vol = volume[-lookback:]
            prev_vol = volume[-lookback * 2:-lookback]

            recent_high = np.max(recent)
            prev_high = np.max(prev)
            recent_vol_avg = np.mean(recent_vol)
            prev_vol_avg = np.mean(prev_vol)

            recent_low = np.min(recent)
            prev_low = np.min(prev)

            # Bearish divergence : prix plus haut, volume plus bas
            if recent_high > prev_high and recent_vol_avg < prev_vol_avg * 0.8:
                divergences.append({
                    "type": "BEARISH",
                    "description": f"Prix plus haut mais volume en baisse (lookback={lookback})",
                    "strength": round((1 - recent_vol_avg / prev_vol_avg) * 100, 1),
                })

            # Bullish divergence : prix plus bas, volume plus bas (selling exhaustion)
            if recent_low < prev_low and recent_vol_avg < prev_vol_avg * 0.8:
                divergences.append({
                    "type": "BULLISH",
                    "description": f"Prix plus bas avec volume decroissant - epuisement vendeur (lookback={lookback})",
                    "strength": round((1 - recent_vol_avg / prev_vol_avg) * 100, 1),
                })

        return divergences[:5]

    # ── Sentiment ──

    def _calculate_sentiment(self, close, volume, high, low) -> tuple:
        """
        Calcule un score de sentiment basé sur l'action des prix.
        0 = Extreme Fear, 100 = Extreme Greed.
        """
        if len(close) < 30:
            return 50, "NEUTRAL"

        score = 50  # Base neutre

        # 1. Momentum (20pts)
        mom_20 = (close[-1] / close[-20] - 1) * 100
        score += np.clip(mom_20 * 2, -20, 20)

        # 2. Volatility (15pts) - haute vol = fear
        returns = np.diff(np.log(close[-30:]))
        vol = np.std(returns) * np.sqrt(365) * 100
        if vol > 80:
            score -= 15
        elif vol > 50:
            score -= 8
        elif vol < 20:
            score += 10

        # 3. Volume trend (10pts)
        vol_ratio = np.mean(volume[-5:]) / np.mean(volume[-20:]) if np.mean(volume[-20:]) > 0 else 1
        if vol_ratio > 1.5 and close[-1] > close[-5]:
            score += 10  # Bullish volume
        elif vol_ratio > 1.5 and close[-1] < close[-5]:
            score -= 10  # Panic selling

        # 4. Distance from high/low (5pts)
        recent_high = np.max(high[-20:])
        recent_low = np.min(low[-20:])
        price_range = recent_high - recent_low
        if price_range > 0:
            position = (close[-1] - recent_low) / price_range
            score += (position - 0.5) * 10

        score = np.clip(score, 0, 100)

        if score >= 80:
            label = "EXTREME_GREED"
        elif score >= 60:
            label = "GREED"
        elif score >= 40:
            label = "NEUTRAL"
        elif score >= 20:
            label = "FEAR"
        else:
            label = "EXTREME_FEAR"

        return score, label

    # ── Key Levels ──

    def _find_key_levels(self, price: float) -> list:
        """Trouve les niveaux psychologiques proches."""
        levels = []

        # Niveaux ronds
        if price > 1000:
            step = 1000
        elif price > 100:
            step = 100
        elif price > 10:
            step = 10
        elif price > 1:
            step = 1
        else:
            step = 0.1

        base = int(price / step) * step
        for mult in [-2, -1, 0, 1, 2, 3]:
            level = base + mult * step
            if level > 0:
                levels.append(level)

        # Demi-niveaux aussi
        for mult in [-1, 0, 1, 2]:
            level = base + mult * step + step / 2
            if level > 0:
                levels.append(level)

        return sorted(set(levels))

    # ── Funding Rate ──

    def _get_funding_rate(self, symbol: str) -> Optional[float]:
        """Recupere le funding rate du perpetual."""
        if not self.exchange:
            return None
        try:
            pair = f"{symbol}/USDT"
            funding = self.exchange.exchange.fetch_funding_rate(pair)
            return round(funding.get("fundingRate", 0) * 100, 4)
        except Exception:
            return None

    def _get_oi_change(self, symbol: str) -> Optional[float]:
        """Recupere le changement d'open interest."""
        # MEXC spot n'a pas d'OI, retourne None
        return None
