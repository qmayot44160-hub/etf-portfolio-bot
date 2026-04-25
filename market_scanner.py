"""
Scanner de marche temps reel - scanne tout le marche MEXC pour trouver
les meilleures opportunites automatiquement.

Pipeline :
1. Filtre les paires USDT avec volume suffisant
2. Pre-filtre rapide : momentum, volume surge, volatilite
3. Analyse approfondie des top candidats
4. Classe par score d'opportunite
5. Met a jour la watchlist dynamique du trader

Inspire des screeners institutionnels (Bloomberg Terminal, Reuters Eikon).
"""

import json
import os
import time
import threading
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Optional

from data_paths import data_path

SCANNER_CONFIG_FILE = data_path("scanner_config.json")
SCANNER_CACHE_FILE = data_path("scanner_cache.json")


@dataclass
class ScanResult:
    symbol: str
    price: float
    score: float  # 0-100 score d'opportunite
    direction: str  # "LONG", "SHORT"
    category: str  # "MOMENTUM", "BREAKOUT", "REVERSAL", "VOLUME_SPIKE"
    volume_24h: float
    volume_change_pct: float
    price_change_1h: float
    price_change_24h: float
    price_change_7d: float
    volatility: float
    momentum_rank: int
    reasons: list
    timestamp: str


class MarketScanner:
    """Scanner de marche complet."""

    def __init__(self, exchange=None):
        self.exchange = exchange
        self.config = self._load_config()
        self.cache = self._load_cache()
        self._scanning = False
        self._last_scan = None
        self._scan_thread = None

    def _load_config(self) -> dict:
        if os.path.exists(SCANNER_CONFIG_FILE):
            with open(SCANNER_CONFIG_FILE, "r") as f:
                return json.load(f)
        return {
            "enabled": True,
            "scan_interval_minutes": 15,
            "min_volume_24h_usdt": 500000,
            "min_price_usdt": 0.0001,
            "max_results": 20,
            "top_for_trading": 10,
            "filters": {
                "exclude_stablecoins": True,
                "exclude_leveraged": True,
                "min_market_cap_rank": 0,
            },
            "scoring_weights": {
                "momentum": 0.25,
                "volume": 0.25,
                "volatility": 0.20,
                "trend": 0.15,
                "reversal_potential": 0.15,
            },
            "blacklist": ["USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "USDD"],
            "auto_update_watchlist": True,
        }

    def save_config(self, config: dict):
        self.config = config
        with open(SCANNER_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)

    def _load_cache(self) -> dict:
        if os.path.exists(SCANNER_CACHE_FILE):
            try:
                with open(SCANNER_CACHE_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"results": [], "last_scan": None, "stats": {}}

    def _save_cache(self):
        with open(SCANNER_CACHE_FILE, "w") as f:
            json.dump(self.cache, f, indent=2)

    # ══════════════════════════════════════════════════════════
    #  SCAN PRINCIPAL
    # ══════════════════════════════════════════════════════════

    def full_scan(self) -> dict:
        """
        Scan complet du marche.
        1. Recupere toutes les paires USDT
        2. Pre-filtre par volume/prix
        3. Analyse rapide (momentum, volume, volatilite)
        4. Analyse approfondie des top candidats
        5. Retourne les meilleures opportunites classees
        """
        if not self.exchange or not self.exchange.connected:
            return {"error": "Exchange non connecte", "results": []}

        self._scanning = True
        start_time = time.time()

        try:
            # Step 1: Get all tickers
            all_tickers = self.exchange.exchange.fetch_tickers()

            # Step 2: Pre-filter
            candidates = self._pre_filter(all_tickers)

            # Step 3: Quick analysis on candidates
            scored = self._quick_score(candidates, all_tickers)

            # Step 4: Sort by score and take top N
            scored.sort(key=lambda x: x["score"], reverse=True)
            top_candidates = scored[:self.config.get("max_results", 20)]

            # Step 5: Deep analysis on top candidates
            results = []
            for candidate in top_candidates:
                try:
                    deep = self._deep_analyze(candidate)
                    results.append(deep)
                except Exception:
                    # Keep quick analysis if deep fails
                    results.append(self._to_scan_result(candidate))

            # Re-sort after deep analysis
            results.sort(key=lambda x: x.score, reverse=True)

            elapsed = round(time.time() - start_time, 1)
            self._last_scan = datetime.now().isoformat()

            # Update cache
            self.cache = {
                "results": [asdict(r) for r in results],
                "last_scan": self._last_scan,
                "stats": {
                    "total_pairs_scanned": len(all_tickers),
                    "after_filter": len(candidates),
                    "top_opportunities": len(results),
                    "scan_duration_seconds": elapsed,
                    "best_opportunity": results[0].symbol if results else None,
                    "best_score": results[0].score if results else 0,
                },
            }
            self._save_cache()

            return {
                "results": [asdict(r) for r in results],
                "stats": self.cache["stats"],
                "timestamp": self._last_scan,
            }

        except Exception as e:
            return {"error": str(e), "results": []}
        finally:
            self._scanning = False

    def _pre_filter(self, tickers: dict) -> list:
        """Filtre les paires par volume, prix, et exclusions."""
        config = self.config
        blacklist = set(config.get("blacklist", []))
        min_vol = config.get("min_volume_24h_usdt", 500000)
        min_price = config.get("min_price_usdt", 0.0001)
        exclude_stable = config.get("filters", {}).get("exclude_stablecoins", True)
        exclude_lev = config.get("filters", {}).get("exclude_leveraged", True)

        candidates = []
        for pair, data in tickers.items():
            # Only USDT pairs
            if "/USDT" not in pair:
                continue

            symbol = pair.replace("/USDT", "")

            # Skip stablecoins
            if symbol in blacklist:
                continue
            if symbol == "USDT":
                continue
            if exclude_stable and symbol in ("USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "USDD", "UST", "FRAX"):
                continue

            # Skip leveraged tokens
            if exclude_lev and any(x in symbol for x in ("3L", "3S", "5L", "5S", "UP", "DOWN", "BULL", "BEAR")):
                continue

            # Volume filter
            quote_vol = float(data.get("quoteVolume", 0) or 0)
            if quote_vol < min_vol:
                continue

            # Price filter
            last_price = float(data.get("last", 0) or 0)
            if last_price < min_price:
                continue

            change_24h = float(data.get("percentage", 0) or 0)
            high_24h = float(data.get("high", 0) or 0)
            low_24h = float(data.get("low", 0) or 0)
            base_vol = float(data.get("baseVolume", 0) or 0)

            candidates.append({
                "symbol": symbol,
                "pair": pair,
                "price": last_price,
                "volume_24h_usdt": quote_vol,
                "volume_24h_base": base_vol,
                "change_24h": change_24h,
                "high_24h": high_24h,
                "low_24h": low_24h,
                "bid": float(data.get("bid", 0) or 0),
                "ask": float(data.get("ask", 0) or 0),
            })

        return candidates

    def _quick_score(self, candidates: list, all_tickers: dict) -> list:
        """Score rapide base sur les donnees ticker (pas d'OHLCV)."""
        if not candidates:
            return []

        # Normalize metrics
        volumes = [c["volume_24h_usdt"] for c in candidates]
        changes = [abs(c["change_24h"]) for c in candidates]

        max_vol = max(volumes) if volumes else 1
        max_change = max(changes) if changes else 1

        for c in candidates:
            price = c["price"]
            high = c["high_24h"]
            low = c["low_24h"]
            change = c["change_24h"]
            vol = c["volume_24h_usdt"]

            # Volatility score (range / price)
            if price > 0 and high > 0 and low > 0:
                range_pct = (high - low) / price * 100
            else:
                range_pct = 0

            # Volume score (normalized)
            vol_score = (vol / max_vol) * 100 if max_vol > 0 else 0

            # Momentum score
            mom_score = min(abs(change) / max_change * 100, 100) if max_change > 0 else 0

            # Price position in 24h range (0=bottom, 100=top)
            if high > low:
                range_position = (price - low) / (high - low) * 100
            else:
                range_position = 50

            # Trend score : prix haut dans le range + momentum positif = bullish
            if change > 2 and range_position > 60:
                trend_score = 70 + min(change, 30)
            elif change < -2 and range_position < 40:
                trend_score = 70 + min(abs(change), 30)  # Bon pour short
            else:
                trend_score = 30

            # Reversal potential : gros mouvement + prix a l'extreme
            reversal_score = 0
            if abs(change) > 5:
                if change > 5 and range_position > 85:
                    reversal_score = min(change * 3, 80)  # Surachat potentiel
                elif change < -5 and range_position < 15:
                    reversal_score = min(abs(change) * 3, 80)  # Survente potentielle

            # Weighted total
            w = self.config.get("scoring_weights", {})
            score = (
                mom_score * w.get("momentum", 0.25) +
                vol_score * w.get("volume", 0.25) +
                min(range_pct * 5, 100) * w.get("volatility", 0.20) +
                trend_score * w.get("trend", 0.15) +
                reversal_score * w.get("reversal_potential", 0.15)
            )

            # Category
            if reversal_score > 50:
                category = "REVERSAL"
            elif abs(change) > 8 and vol_score > 60:
                category = "MOMENTUM"
            elif range_pct > 10 and vol_score > 50:
                category = "BREAKOUT"
            elif vol_score > 70:
                category = "VOLUME_SPIKE"
            else:
                category = "MOMENTUM"

            # Direction
            if change > 1 and range_position > 50:
                direction = "LONG"
            elif change < -1 and range_position < 50:
                direction = "SHORT"
            elif reversal_score > 50 and change > 5:
                direction = "SHORT"  # Reversal from overbought
            elif reversal_score > 50 and change < -5:
                direction = "LONG"  # Reversal from oversold
            else:
                direction = "LONG" if change > 0 else "SHORT"

            c["score"] = round(score, 1)
            c["category"] = category
            c["direction"] = direction
            c["range_pct"] = round(range_pct, 2)
            c["range_position"] = round(range_position, 1)
            c["vol_score"] = round(vol_score, 1)
            c["mom_score"] = round(mom_score, 1)
            c["trend_score"] = round(trend_score, 1)

        return candidates

    def _deep_analyze(self, candidate: dict) -> ScanResult:
        """Analyse approfondie avec OHLCV."""
        symbol = candidate["symbol"]

        try:
            pair = f"{symbol}/USDT"
            ohlcv_1h = self.exchange.exchange.fetch_ohlcv(pair, "1h", limit=72)
            df = pd.DataFrame(ohlcv_1h, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

            close = df["close"].values
            volume = df["volume"].values

            # 1h price change
            price_change_1h = (close[-1] / close[-2] - 1) * 100 if len(close) > 1 else 0

            # 7d approximation (72h)
            price_change_7d = (close[-1] / close[0] - 1) * 100 if len(close) > 0 else 0

            # Realized volatility
            returns = np.diff(np.log(close))
            volatility = np.std(returns) * np.sqrt(24 * 365) * 100 if len(returns) > 2 else 0

            # Volume trend
            if len(volume) >= 24:
                vol_recent = np.mean(volume[-6:])
                vol_avg = np.mean(volume[-24:])
                vol_change = (vol_recent / vol_avg - 1) * 100 if vol_avg > 0 else 0
            else:
                vol_change = 0

            # RSI rapide (14 periodes)
            rsi = self._quick_rsi(close, 14)

            # EMA trend
            ema_9 = self._ema(close, 9)
            ema_21 = self._ema(close, 21)
            trend_bullish = ema_9 > ema_21 if ema_9 and ema_21 else False

            # Refine score with OHLCV data
            reasons = []
            score_adj = candidate["score"]

            # Volume surge bonus
            if vol_change > 100:
                score_adj += 15
                reasons.append(f"Volume surge +{vol_change:.0f}%")
            elif vol_change > 50:
                score_adj += 8
                reasons.append(f"Volume en hausse +{vol_change:.0f}%")

            # RSI extremes
            if rsi < 30:
                score_adj += 10
                reasons.append(f"RSI survendu ({rsi:.0f})")
                candidate["direction"] = "LONG"
                candidate["category"] = "REVERSAL"
            elif rsi > 70:
                score_adj += 5
                reasons.append(f"RSI surachete ({rsi:.0f})")

            # Strong trend
            if trend_bullish and candidate["change_24h"] > 3:
                score_adj += 10
                reasons.append("Tendance haussiere confirmee (EMA 9 > 21)")
            elif not trend_bullish and candidate["change_24h"] < -3:
                score_adj += 10
                reasons.append("Tendance baissiere confirmee (EMA 9 < 21)")

            # Momentum acceleration
            if len(close) >= 6:
                mom_recent = close[-1] / close[-3] - 1
                mom_prev = close[-3] / close[-6] - 1
                if mom_recent > mom_prev and mom_recent > 0.01:
                    score_adj += 8
                    reasons.append("Acceleration du momentum")

            # Volatility sweet spot (enough to profit, not too crazy)
            if 30 < volatility < 150:
                score_adj += 5
                reasons.append(f"Volatilite favorable ({volatility:.0f}%)")
            elif volatility > 200:
                score_adj -= 10
                reasons.append(f"Volatilite extreme ({volatility:.0f}%) - risque eleve")

            # Price action
            if candidate["change_24h"] > 5:
                reasons.append(f"Hausse 24h: +{candidate['change_24h']:.1f}%")
            elif candidate["change_24h"] < -5:
                reasons.append(f"Baisse 24h: {candidate['change_24h']:.1f}%")

            reasons.append(f"Vol 24h: ${candidate['volume_24h_usdt']:,.0f}")

            score_adj = max(0, min(100, score_adj))

            return ScanResult(
                symbol=symbol,
                price=candidate["price"],
                score=round(score_adj, 1),
                direction=candidate["direction"],
                category=candidate["category"],
                volume_24h=candidate["volume_24h_usdt"],
                volume_change_pct=round(vol_change, 1),
                price_change_1h=round(price_change_1h, 2),
                price_change_24h=round(candidate["change_24h"], 2),
                price_change_7d=round(price_change_7d, 2),
                volatility=round(volatility, 1),
                momentum_rank=0,  # Set later
                reasons=reasons,
                timestamp=datetime.now().isoformat(),
            )

        except Exception:
            return self._to_scan_result(candidate)

    def _to_scan_result(self, candidate: dict) -> ScanResult:
        """Convertit un candidat en ScanResult basique."""
        reasons = []
        if candidate["change_24h"] > 5:
            reasons.append(f"Hausse 24h: +{candidate['change_24h']:.1f}%")
        elif candidate["change_24h"] < -5:
            reasons.append(f"Baisse 24h: {candidate['change_24h']:.1f}%")
        reasons.append(f"Vol 24h: ${candidate['volume_24h_usdt']:,.0f}")

        return ScanResult(
            symbol=candidate["symbol"],
            price=candidate["price"],
            score=candidate.get("score", 0),
            direction=candidate.get("direction", "LONG"),
            category=candidate.get("category", "MOMENTUM"),
            volume_24h=candidate["volume_24h_usdt"],
            volume_change_pct=0,
            price_change_1h=0,
            price_change_24h=candidate["change_24h"],
            price_change_7d=0,
            volatility=candidate.get("range_pct", 0) * 10,
            momentum_rank=0,
            reasons=reasons,
            timestamp=datetime.now().isoformat(),
        )

    def _quick_rsi(self, close, period=14) -> float:
        if len(close) < period + 1:
            return 50
        deltas = np.diff(close)[-period:]
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)

    def _ema(self, data, period) -> Optional[float]:
        if len(data) < period:
            return None
        multiplier = 2 / (period + 1)
        ema = data[0]
        for price in data[1:]:
            ema = (price - ema) * multiplier + ema
        return ema

    # ══════════════════════════════════════════════════════════
    #  TOP MOVERS / CATEGORIES
    # ══════════════════════════════════════════════════════════

    def get_top_movers(self) -> dict:
        """Recupere les top movers sans scan complet (rapide)."""
        if not self.exchange or not self.exchange.connected:
            return {"error": "Exchange non connecte"}

        try:
            tickers = self.exchange.exchange.fetch_tickers()
            candidates = self._pre_filter(tickers)

            # Top gainers
            gainers = sorted(candidates, key=lambda x: x["change_24h"], reverse=True)[:10]
            # Top losers
            losers = sorted(candidates, key=lambda x: x["change_24h"])[:10]
            # Top volume
            by_volume = sorted(candidates, key=lambda x: x["volume_24h_usdt"], reverse=True)[:10]
            # Most volatile (range)
            for c in candidates:
                if c["price"] > 0 and c["high_24h"] > 0:
                    c["range_pct"] = (c["high_24h"] - c["low_24h"]) / c["price"] * 100
                else:
                    c["range_pct"] = 0
            volatile = sorted(candidates, key=lambda x: x.get("range_pct", 0), reverse=True)[:10]

            def fmt(items):
                return [{
                    "symbol": c["symbol"],
                    "price": c["price"],
                    "change_24h": round(c["change_24h"], 2),
                    "volume_24h": round(c["volume_24h_usdt"], 0),
                    "range_pct": round(c.get("range_pct", 0), 2),
                } for c in items]

            return {
                "gainers": fmt(gainers),
                "losers": fmt(losers),
                "by_volume": fmt(by_volume),
                "most_volatile": fmt(volatile),
                "total_pairs": len(candidates),
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            return {"error": str(e)}

    def get_cached_results(self) -> dict:
        """Retourne les resultats du dernier scan."""
        return self.cache

    def get_dynamic_watchlist(self) -> list:
        """
        Retourne la watchlist dynamique : les meilleurs symboles a trader.
        Combine les resultats du scan avec les tokens fixes de la config.
        """
        top_n = self.config.get("top_for_trading", 10)
        results = self.cache.get("results", [])

        # Top symbols from scan
        dynamic = []
        for r in results[:top_n]:
            if r.get("score", 0) >= 30:  # Seuil minimum
                dynamic.append(r["symbol"])

        return dynamic

    # ══════════════════════════════════════════════════════════
    #  BACKGROUND SCANNING
    # ══════════════════════════════════════════════════════════

    def start_background_scan(self):
        """Lance le scan en arriere-plan."""
        if self._scan_thread and self._scan_thread.is_alive():
            return {"status": "already_running"}

        self._scan_thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._scan_thread.start()
        return {"status": "started"}

    def _scan_loop(self):
        """Boucle de scan periodique."""
        while True:
            if self.config.get("enabled", True) and self.exchange and self.exchange.connected:
                try:
                    self.full_scan()
                except Exception as e:
                    print(f"[MarketScanner] Erreur: {e}")

            interval = self.config.get("scan_interval_minutes", 15) * 60
            time.sleep(interval)
