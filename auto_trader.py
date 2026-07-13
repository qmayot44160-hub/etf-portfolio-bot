"""
Auto-trader institutionnel - niveau hedge fund.

Architecture :
1. Quant Models : detection de regime, multi-strategie, scoring
2. Risk Engine : VaR, circuit breakers, position sizing Kelly
3. Smart Execution : TWAP, slippage estimation, order splitting
4. Market Intelligence : structure, whales, divergences, sentiment
5. Trailing stop avance + multi-timeframe

Pipeline de decision :
  Data → Regime Detection → Multi-Strategy Scoring → Risk Check → Smart Execution → Monitoring
"""

import json
import os
import time
import threading
from datetime import datetime
from dataclasses import dataclass, asdict, field, MISSING
from typing import Optional
from config import CRYPTO_PORTFOLIO
from trading_strategy import TradingStrategy
from quant_models import QuantModels
from risk_engine import RiskEngine
from smart_execution import SmartExecution
from market_intelligence import MarketIntelligence
from market_scanner import MarketScanner
from probability_engine import ProbabilityEngine
from multi_horizon import MultiHorizonEngine
import prediction_log
from data_paths import data_path
import notifications as notif

TRADES_FILE = data_path("trades_history.json")
ACTIVE_TRADES_FILE = data_path("active_trades.json")
TRADER_CONFIG_FILE = data_path("trader_config.json")
PAPER_STATE_FILE = data_path("paper_trading_state.json")


@dataclass
class ActiveTrade:
    symbol: str
    side: str  # "BUY" ou "SELL"
    entry_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    initial_stop_loss: float = 0
    highest_price: float = 0
    lowest_price: float = 999999
    trailing_active: bool = False
    order_id: str = ""
    opened_at: str = ""
    status: str = "OPEN"
    pnl: float = 0
    pnl_pct: float = 0
    closed_at: str = ""
    close_price: float = 0
    timeframes_confirmed: list = field(default_factory=list)
    regime: str = ""
    confidence: float = 0
    strategies_agreed: int = 0
    risk_score: int = 0
    execution_slippage: float = 0


class AutoTrader:
    def __init__(self, exchange=None):
        self.exchange = exchange
        self.strategy = TradingStrategy()
        self.risk_engine = RiskEngine()
        self.smart_exec = SmartExecution(exchange)
        self.market_intel = MarketIntelligence(exchange)
        self.scanner = MarketScanner(exchange)
        self.prob_engine = ProbabilityEngine()
        self.mh_engine = MultiHorizonEngine()
        self._last_model_train_ts = 0.0   # timestamp du dernier auto-entrainement
        self.active_trades: list[ActiveTrade] = []
        self.trade_history: list = []
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._load_state()

    # ── Paper trading helpers ─────────────────────────────────

    def _load_paper_state(self) -> dict:
        """Charge le state paper (capital + equity curve) depuis disque."""
        if os.path.exists(PAPER_STATE_FILE):
            try:
                with open(PAPER_STATE_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        cap = self.get_config().get("trading_capital", 10000.0)
        return {"capital": cap, "initial_capital": cap, "equity": []}

    def _save_paper_state(self, state: dict):
        try:
            with open(PAPER_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"[AutoTrader] paper state save error: {e}")

    def _paper_close(self, trade: ActiveTrade, exit_price: float, reason: str):
        """
        Clôture un trade en mode paper : calcule le P&L, met à jour
        le capital paper et l'equity curve. Pas d'ordre réel.
        """
        if trade.side == "BUY":
            trade.pnl = round((exit_price - trade.entry_price) * trade.quantity, 2)
        else:
            trade.pnl = round((trade.entry_price - exit_price) * trade.quantity, 2)
        notional = (trade.entry_price + exit_price) * trade.quantity
        # Frais + slippage : même source que le backtest (cohérence paper/backtest)
        from strategy_backtest import DEFAULT_FEE_PCT, DEFAULT_SLIPPAGE
        cost = notional * (DEFAULT_FEE_PCT + DEFAULT_SLIPPAGE) / 100.0
        trade.pnl = round(trade.pnl - cost, 2)
        trade.pnl_pct = round(trade.pnl / (trade.entry_price * trade.quantity) * 100, 2)
        trade.close_price = exit_price
        trade.status = reason
        trade.closed_at = datetime.now().isoformat()

        ps = self._load_paper_state()
        ps["capital"] = round(ps["capital"] + trade.pnl, 2)
        ps.setdefault("equity", []).append({
            "ts": trade.closed_at,
            "capital": ps["capital"],
            "pnl": trade.pnl,
            "symbol": trade.symbol,
        })
        self._save_paper_state(ps)

    def get_paper_performance(self) -> dict:
        """Retourne les métriques paper trading pour l'UI."""
        ps = self._load_paper_state()
        equity = ps.get("equity", [])
        initial = ps.get("initial_capital", 10000.0)
        capital = ps.get("capital", initial)
        closed = [t for t in self.trade_history if t.get("status") in
                  ("TP_HIT", "SL_HIT", "TRAILING_SL", "MANUAL_CLOSE")]
        wins = [t for t in closed if (t.get("pnl") or 0) > 0]
        return {
            "initial_capital":  initial,
            "current_capital":  capital,
            "return_pct":       round((capital / initial - 1) * 100, 2) if initial > 0 else 0,
            "total_trades":     len(closed),
            "win_rate":         round(len(wins) / len(closed) * 100, 1) if closed else 0,
            "total_pnl":        round(sum(t.get("pnl", 0) for t in closed), 2),
            "equity":           equity[-200:],   # 200 derniers points
        }

    def reset_paper_trading(self):
        """Remet le capital paper à son niveau initial."""
        cap = self.get_config().get("trading_capital", 10000.0)
        self._save_paper_state({"capital": cap, "initial_capital": cap, "equity": []})

    def _load_state(self):
        if os.path.exists(ACTIVE_TRADES_FILE):
            try:
                with open(ACTIVE_TRADES_FILE, "r") as f:
                    data = json.load(f)
                self.active_trades = []
                for t in data:
                    for k, fld in ActiveTrade.__dataclass_fields__.items():
                        if k not in t:
                            if fld.default is not MISSING:
                                t[k] = fld.default
                            elif fld.default_factory is not MISSING:
                                t[k] = fld.default_factory()
                    self.active_trades.append(ActiveTrade(**{
                        k: v for k, v in t.items()
                        if k in ActiveTrade.__dataclass_fields__
                    }))
            except Exception as e:
                print(f"[AutoTrader] _load_state active_trades error: {e}")
                self.active_trades = []
        if os.path.exists(TRADES_FILE):
            try:
                with open(TRADES_FILE, "r") as f:
                    self.trade_history = json.load(f)
            except Exception as e:
                print(f"[AutoTrader] _load_state trade_history error: {e}")
                self.trade_history = []

    def _save_state(self):
        try:
            with open(ACTIVE_TRADES_FILE, "w") as f:
                json.dump([asdict(t) for t in self.active_trades], f, indent=2)
            with open(TRADES_FILE, "w") as f:
                json.dump(self.trade_history[-500:], f, indent=2)
        except Exception as e:
            print(f"[AutoTrader] _save_state error: {e}")

    def get_config(self) -> dict:
        # Fusionne les défauts avec le fichier sauvegardé : les nouvelles clés
        # (ajoutées au fil des versions) apparaissent toujours avec leur défaut,
        # les valeurs de l'utilisateur ont priorité.
        defaults = self._default_config()
        if os.path.exists(TRADER_CONFIG_FILE):
            try:
                with open(TRADER_CONFIG_FILE, "r") as f:
                    loaded = json.load(f)
                return {**defaults, **loaded}
            except Exception:
                return defaults
        return defaults

    def _default_config(self) -> dict:
        return {
            "enabled": False,
            "symbols": list(CRYPTO_PORTFOLIO.keys()),
            "timeframe": "1h",
            "confirm_timeframes": ["1h", "4h"],
            "check_interval": 300,
            "max_open_trades": 5,
            "risk_per_trade_pct": 2.0,
            "sl_atr_multiplier": 1.5,
            "tp_atr_multiplier": 3.0,
            "trailing_stop": True,
            "trailing_stop_pct": 2.0,
            "trailing_activation_pct": 1.0,
            "only_strong_signals": False,
            # Institutional settings
            "use_quant_models": True,
            "use_risk_engine": True,
            "use_smart_execution": True,
            "use_market_intel": True,
            "use_market_scanner": True,
            "scanner_mode": "hybrid",  # "fixed", "dynamic", "hybrid"
            "min_confidence": 55,           # seuil qualité signal (relevé de 40→55)
            "min_probability": 0.0,         # gate proba calibrée 0-1 (0 = désactivé, ex: 0.6 = 60%)
            "auto_train_models": True,      # auto-entraînement ML en tâche de fond
            "model_refresh_days": 7,        # ré-entraînement périodique (jours)
            "min_strategies_agree": 2,
            "execution_urgency": "normal",  # low, normal, high
            "trading_capital": 10000.0,     # capital de référence pour les limites
            "max_daily_loss_pct": 3.0,      # auto kill-switch si perte jour > X% (0 = désactivé)
            "paper_mode": True,             # paper mode par défaut (pas d'ordres réels)
        }

    def save_config(self, config: dict):
        with open(TRADER_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)

    def get_status(self) -> dict:
        live = bool(self.exchange and self.exchange.connected)
        cfg = self.get_config()
        paper = cfg.get("paper_mode", True)
        return {
            "running": self.running,
            "live_mode": live and not paper,
            "paper_mode": paper,
            "mode": "PAPER" if paper else ("LIVE" if live else "SIMULATION"),
            "active_trades": len(self.active_trades),
            "total_trades": len(self.trade_history),
            "config": cfg,
        }

    def get_active_trades(self) -> list:
        if self.exchange and self.exchange.connected:
            for trade in self.active_trades:
                if trade.status != "OPEN":
                    continue
                try:
                    price = self.exchange.get_ticker_price(trade.symbol)
                    if trade.side == "BUY":
                        trade.pnl = round((price - trade.entry_price) * trade.quantity, 2)
                    else:
                        trade.pnl = round((trade.entry_price - price) * trade.quantity, 2)
                    trade.pnl_pct = round(trade.pnl / (trade.entry_price * trade.quantity) * 100, 2)
                except Exception:
                    pass
        return [asdict(t) for t in self.active_trades]

    def get_trade_history(self) -> list:
        return self.trade_history[-100:]

    # ══════════════════════════════════════════════════════════
    #  ANALYSE INSTITUTIONNELLE
    # ══════════════════════════════════════════════════════════

    def _fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100):
        import pandas as pd
        # Normalisation idempotente : accepte "BTC" comme "BTC/USDT" sans doubler
        # le suffixe (evite "BTC/USDT/USDT" quand un appelant passe deja la paire).
        base = symbol.upper().split("/")[0]
        pair = f"{base}/USDT"
        ohlcv = self.exchange.exchange.fetch_ohlcv(pair, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df

    def analyze_symbol(self, symbol: str, timeframe: str = "1h") -> dict:
        """Analyse technique classique d'un symbole."""
        if not self.exchange or not self.exchange.connected:
            return {"symbol": symbol, "error": "Exchange non connecte"}
        try:
            df = self._fetch_ohlcv(symbol, timeframe)
            signal = self.strategy.analyze(df, symbol)
            out = {
                "symbol": symbol,
                "signal": signal.signal.value,
                "price": signal.price,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "confidence": signal.confidence,
                "reasons": signal.reasons,
                "indicators": signal.indicators,
                "timeframe": timeframe,
                "timestamp": datetime.now().isoformat(),
            }
            # Probabilité calibrée si le modèle est entraîné (réutilise le df)
            try:
                if self.prob_engine.is_ready():
                    pred = self.prob_engine.predict(df)
                    if pred.get("available"):
                        out["probability"] = pred
            except Exception:
                pass
            return out
        except Exception as e:
            return {"symbol": symbol, "error": str(e)}

    def analyze_institutional(self, symbol: str) -> dict:
        """
        Analyse institutionnelle complete d'un symbole.
        Combine: Quant Models + Market Intel + Technical Analysis + Multi-TF.
        """
        if not self.exchange or not self.exchange.connected:
            return {"symbol": symbol, "error": "Exchange non connecte"}

        config = self.get_config()

        try:
            # 1. Fetch data (plus long historique pour les modeles quant)
            df = self._fetch_ohlcv(symbol, config["timeframe"], limit=200)
            if len(df) < 50:
                return {"symbol": symbol, "error": "Pas assez de donnees"}

            result = {
                "symbol": symbol,
                "timestamp": datetime.now().isoformat(),
                "institutional": True,
            }

            # 2. Technical analysis (base)
            ta = self.strategy.analyze(df, symbol)
            result["technical"] = {
                "signal": ta.signal.value,
                "price": ta.price,
                "stop_loss": ta.stop_loss,
                "take_profit": ta.take_profit,
                "confidence": ta.confidence,
                "indicators": ta.indicators,
                "reasons": ta.reasons,
            }

            # 2b. Probabilité calibrée (réutilise le df déjà chargé)
            try:
                if self.prob_engine.is_ready():
                    pred = self.prob_engine.predict(df)
                    if pred.get("available"):
                        result["probability"] = pred
            except Exception:
                pass

            # 3. Quant Models - regime + multi-strategy
            if config.get("use_quant_models", True):
                quant = QuantModels.generate_quant_signal(df, symbol, self.trade_history)
                regime = QuantModels.detect_regime(df)
                result["quant"] = {
                    "direction": quant.direction,
                    "edge": quant.edge,
                    "confidence": quant.confidence,
                    "regime": regime.regime.value,
                    "regime_confidence": regime.confidence,
                    "hurst": regime.hurst_exponent,
                    "trend_strength": regime.trend_strength,
                    "volatility_regime": regime.volatility_regime,
                    "mean_reversion_zscore": regime.mean_reversion_score,
                    "strategies_agree": quant.strategies_agree,
                    "strategies_total": quant.strategies_total,
                    "kelly_size": quant.position_size_kelly,
                    "signals": {
                        name: {
                            "signal": s["signal"],
                            "weight": round(s["weight"], 2),
                        }
                        for name, s in quant.signals.items()
                    },
                }
            else:
                result["quant"] = None

            # 4. Market Intelligence
            if config.get("use_market_intel", True):
                intel = self.market_intel.full_analysis(df, symbol)
                result["intelligence"] = {
                    "sentiment": intel.sentiment,
                    "sentiment_score": intel.sentiment_score,
                    "whale_activity": intel.whale_activity,
                    "volume_trend": intel.volume_trend,
                    "market_structure": intel.market_structure,
                    "support_levels": intel.support_levels[:3],
                    "resistance_levels": intel.resistance_levels[:3],
                    "divergences": intel.divergences,
                    "funding_rate": intel.funding_rate,
                    "market_fng": intel.market_fng,
                    "market_fng_label": intel.market_fng_label,
                }
            else:
                result["intelligence"] = None

            # 5. Multi-timeframe confirmation
            confirmations = []
            confirm_tfs = config.get("confirm_timeframes", ["1h", "4h"])
            for tf in confirm_tfs:
                if tf == config["timeframe"]:
                    continue
                try:
                    tf_result = self.analyze_symbol(symbol, tf)
                    confirmations.append({
                        "timeframe": tf,
                        "signal": tf_result.get("signal", "HOLD"),
                        "confidence": tf_result.get("confidence", 0),
                    })
                except Exception:
                    pass

            # 6. Combine everything into final signal
            final = self._combine_signals(result, confirmations, config)
            result["final_signal"] = final["signal"]
            result["final_confidence"] = final["confidence"]
            result["confirmations"] = confirmations
            result["all_confirmed"] = final["all_confirmed"]
            result["price"] = ta.price
            result["stop_loss"] = ta.stop_loss
            result["take_profit"] = ta.take_profit
            result["signal"] = final["signal"]
            result["confidence"] = final["confidence"]
            result["reasons"] = final["reasons"]

            # 7. Slippage estimation
            if config.get("use_smart_execution", True):
                self.smart_exec.exchange = self.exchange
                slip = self.smart_exec.estimate_slippage(symbol, 0.01, ta.price)
                result["execution"] = {
                    "expected_slippage": slip.expected_slippage_pct,
                    "impact": slip.impact_score,
                    "strategy": slip.recommended_strategy,
                }

            return result

        except Exception as e:
            return {"symbol": symbol, "error": str(e)}

    def _combine_signals(self, analysis: dict, confirmations: list, config: dict) -> dict:
        """
        Combine tous les signaux en une decision finale.
        Comme un comite d'investissement qui vote.
        """
        votes = []
        reasons = []

        # Vote 1: Technical Analysis
        ta = analysis.get("technical", {})
        ta_signal = ta.get("signal", "HOLD")
        ta_conf = ta.get("confidence", 0)
        if ta_signal in ("STRONG_BUY", "BUY"):
            votes.append(("TA", 1, ta_conf, 0.25))
            reasons.append(f"AT: {ta_signal} ({ta_conf}%)")
        elif ta_signal in ("STRONG_SELL", "SELL"):
            votes.append(("TA", -1, ta_conf, 0.25))
            reasons.append(f"AT: {ta_signal} ({ta_conf}%)")
        else:
            votes.append(("TA", 0, ta_conf, 0.25))
            reasons.append("TA: HOLD")

        # Vote 2: Quant Models
        quant = analysis.get("quant")
        if quant:
            q_dir = quant.get("direction", "FLAT")
            q_conf = quant.get("confidence", 0)
            q_agree = quant.get("strategies_agree", 0)
            if q_dir == "LONG":
                votes.append(("QUANT", 1, q_conf, 0.35))
                reasons.append(f"Quant: LONG ({q_agree}/{quant.get('strategies_total', 5)} strats, {q_conf}%)")
            elif q_dir == "SHORT":
                votes.append(("QUANT", -1, q_conf, 0.35))
                reasons.append(f"Quant: SHORT ({q_agree}/{quant.get('strategies_total', 5)} strats, {q_conf}%)")
            else:
                votes.append(("QUANT", 0, q_conf, 0.35))
                reasons.append(f"Quant: FLAT")

        # Vote 3: Market Intelligence
        intel = analysis.get("intelligence")
        if intel:
            structure = intel.get("market_structure", "RANGING")
            sentiment_score = intel.get("sentiment_score", 50)
            if structure == "BULLISH" and sentiment_score > 40:
                votes.append(("INTEL", 1, sentiment_score, 0.2))
                reasons.append(f"Intel: Structure {structure}, Sentiment {intel.get('sentiment')}")
            elif structure == "BEARISH" and sentiment_score < 60:
                votes.append(("INTEL", -1, 100 - sentiment_score, 0.2))
                reasons.append(f"Intel: Structure {structure}, Sentiment {intel.get('sentiment')}")
            else:
                votes.append(("INTEL", 0, 50, 0.2))
                reasons.append(f"Intel: Structure {structure}")

            # Whale activity bonus
            if intel.get("whale_activity") == "HIGH":
                reasons.append("Activite whale ELEVEE detectee")

            # Divergences warning
            for div in intel.get("divergences", []):
                reasons.append(f"Divergence {div['type']}: {div['description']}")

        # Vote 4: Multi-timeframe confirmation
        all_confirmed = True
        for conf in confirmations:
            conf_signal = conf.get("signal", "HOLD")
            agrees = True
            primary_bullish = any(v[1] > 0 for v in votes if v[0] in ("TA", "QUANT"))
            primary_bearish = any(v[1] < 0 for v in votes if v[0] in ("TA", "QUANT"))

            if primary_bullish:
                agrees = conf_signal in ("STRONG_BUY", "BUY", "HOLD")
            elif primary_bearish:
                agrees = conf_signal in ("STRONG_SELL", "SELL", "HOLD")

            conf["confirms"] = agrees
            if not agrees:
                all_confirmed = False

        if confirmations:
            mtf_score = sum(1 for c in confirmations if c.get("confirms")) / len(confirmations)
            votes.append(("MTF", 1 if mtf_score > 0.5 else -1 if mtf_score < 0.5 else 0, mtf_score * 100, 0.2))
            reasons.append(f"MTF: {sum(1 for c in confirmations if c.get('confirms'))}/{len(confirmations)} confirmes")

        # Weighted vote
        total_weight = sum(v[3] for v in votes)
        weighted_sum = sum(v[1] * v[2] * v[3] for v in votes) / total_weight if total_weight > 0 else 0
        weighted_confidence = sum(v[2] * v[3] for v in votes) / total_weight if total_weight > 0 else 0

        # Final decision
        if weighted_sum > 30:
            if weighted_sum > 60:
                signal = "STRONG_BUY"
            else:
                signal = "BUY"
        elif weighted_sum < -30:
            if weighted_sum < -60:
                signal = "STRONG_SELL"
            else:
                signal = "SELL"
        else:
            signal = "HOLD"

        # Downgrade si pas de confirmation multi-TF
        if not all_confirmed and config.get("only_strong_signals"):
            signal = "HOLD"
            reasons.append("Signal annule - pas de confirmation multi-TF")

        # Minimum confidence gate
        if weighted_confidence < config.get("min_confidence", 40):
            signal = "HOLD"
            reasons.append(f"Confiance insuffisante ({weighted_confidence:.0f}% < {config.get('min_confidence', 40)}%)")

        # Gate probabiliste (Module 11 des docs : ne trader que si proba >= seuil).
        # Actif seulement si le modèle est entraîné ET qu'un seuil est configuré (>0).
        # Désactivé par défaut → aucun changement de comportement tant que non activé.
        min_prob = config.get("min_probability", 0.0) or 0.0
        prob = analysis.get("probability", {})
        prob_up = prob.get("prob_up") if isinstance(prob, dict) else None
        if min_prob > 0 and prob_up is not None and signal != "HOLD":
            # Probabilité dans le sens du trade
            if signal in ("STRONG_BUY", "BUY"):
                dir_prob = prob_up
            else:
                dir_prob = 1.0 - prob_up
            if dir_prob < min_prob:
                reasons.append(
                    f"Probabilité calibrée insuffisante ({dir_prob*100:.0f}% < {min_prob*100:.0f}%) - trade évité"
                )
                signal = "HOLD"
            else:
                reasons.append(f"Probabilité calibrée {dir_prob*100:.0f}% >= seuil {min_prob*100:.0f}%")

        return {
            "signal": signal,
            "confidence": round(weighted_confidence, 1),
            "weighted_score": round(weighted_sum, 1),
            "all_confirmed": all_confirmed,
            "reasons": reasons,
        }

    def analyze_multi_timeframe(self, symbol: str) -> dict:
        """Backward-compatible multi-timeframe analysis."""
        config = self.get_config()
        if config.get("use_quant_models"):
            return self.analyze_institutional(symbol)
        # Fallback classique
        return self._analyze_multi_tf_classic(symbol)

    def _analyze_multi_tf_classic(self, symbol: str) -> dict:
        """Analyse multi-TF classique (pre-institutional)."""
        if not self.exchange or not self.exchange.connected:
            return {"symbol": symbol, "error": "Exchange non connecte"}

        config = self.get_config()
        timeframes = config.get("confirm_timeframes", ["1h", "4h"])
        results = {}
        for tf in timeframes:
            try:
                results[tf] = self.analyze_symbol(symbol, tf)
            except Exception as e:
                results[tf] = {"symbol": symbol, "timeframe": tf, "error": str(e)}

        primary_tf = timeframes[0]
        primary = results.get(primary_tf, {})
        if primary.get("error"):
            return primary

        primary_signal = primary.get("signal", "HOLD")
        confirmations = []
        for tf in timeframes[1:]:
            r = results.get(tf, {})
            tf_signal = r.get("signal", "HOLD")
            agrees = False
            if primary_signal in ("STRONG_BUY", "BUY"):
                agrees = tf_signal in ("STRONG_BUY", "BUY", "HOLD")
            elif primary_signal in ("STRONG_SELL", "SELL"):
                agrees = tf_signal in ("STRONG_SELL", "SELL", "HOLD")
            else:
                agrees = True
            confirmations.append({"timeframe": tf, "signal": tf_signal, "confirms": agrees})

        all_confirmed = all(c["confirms"] for c in confirmations)
        confidence = primary.get("confidence", 0)
        if all_confirmed and len(confirmations) > 0:
            confidence = min(confidence + 20, 100)
        elif not all_confirmed:
            confidence = max(confidence - 30, 0)
            if config.get("only_strong_signals"):
                primary_signal = "HOLD"

        return {
            **primary,
            "signal": primary_signal if all_confirmed or not config.get("only_strong_signals") else "HOLD",
            "confidence": round(confidence, 1),
            "multi_timeframe": True,
            "confirmations": confirmations,
            "all_confirmed": all_confirmed,
        }

    def analyze_all(self) -> list:
        """Analyse tous les symboles (fixes + dynamiques du scanner)."""
        config = self.get_config()
        mode = config.get("scanner_mode", "fixed")

        # Determine symbols to analyze
        fixed_symbols = set(config.get("symbols", []))

        if mode == "dynamic" and config.get("use_market_scanner"):
            # Only scanner results
            self.scanner.exchange = self.exchange
            dynamic = self.scanner.get_dynamic_watchlist()
            symbols = list(set(dynamic)) if dynamic else list(fixed_symbols)
        elif mode == "hybrid" and config.get("use_market_scanner"):
            # Fixed + top from scanner
            self.scanner.exchange = self.exchange
            dynamic = self.scanner.get_dynamic_watchlist()
            symbols = list(fixed_symbols | set(dynamic[:10]))
        else:
            # Fixed only
            symbols = list(fixed_symbols)

        results = []
        for symbol in symbols:
            try:
                if config.get("use_quant_models"):
                    result = self.analyze_institutional(symbol)
                elif len(config.get("confirm_timeframes", [])) > 1:
                    result = self.analyze_multi_timeframe(symbol)
                else:
                    result = self.analyze_symbol(symbol, config["timeframe"])

                # Enrich with scanner data
                scan_cache = self.scanner.cache.get("results", [])
                scan_match = next((s for s in scan_cache if s.get("symbol") == symbol), None)
                if scan_match:
                    result["scanner"] = {
                        "score": scan_match.get("score", 0),
                        "category": scan_match.get("category", ""),
                        "volume_24h": scan_match.get("volume_24h", 0),
                        "price_change_24h": scan_match.get("price_change_24h", 0),
                        "volatility": scan_match.get("volatility", 0),
                        "from_scanner": True,
                    }
                else:
                    result["scanner"] = {"from_scanner": symbol not in fixed_symbols}

                result["is_dynamic"] = symbol not in fixed_symbols
                results.append(result)
            except Exception as e:
                results.append({"symbol": symbol, "error": str(e)})

        # Sort by confidence/score
        results.sort(key=lambda x: x.get("confidence", 0), reverse=True)
        return results

    # ══════════════════════════════════════════════════════════
    #  EXECUTION AVEC RISK MANAGEMENT
    # ══════════════════════════════════════════════════════════

    def execute_signal(self, analysis: dict) -> dict:
        if not self.exchange or not self.exchange.connected:
            return {"error": "Exchange non connecte"}

        config = self.get_config()
        symbol = analysis["symbol"]
        signal = analysis.get("signal", analysis.get("final_signal", "HOLD"))

        # Check existing trade
        existing = [t for t in self.active_trades if t.symbol == symbol and t.status == "OPEN"]
        if existing:
            return {"status": "SKIP", "reason": f"Trade deja ouvert sur {symbol}"}

        open_count = len([t for t in self.active_trades if t.status == "OPEN"])
        if open_count >= config["max_open_trades"]:
            return {"status": "SKIP", "reason": f"Max trades ouverts ({config['max_open_trades']})"}

        if signal not in ("STRONG_BUY", "BUY", "STRONG_SELL", "SELL"):
            return {"status": "SKIP", "reason": "Signal HOLD - pas d'action"}

        side = "BUY" if signal in ("STRONG_BUY", "BUY") else "SELL"

        # Position sizing
        paper_mode = config.get("paper_mode", True)
        try:
            price = analysis.get("price", 0)
            sl = analysis.get("stop_loss", 0)

            if not price or not sl:
                return {"status": "SKIP", "reason": "Prix ou SL manquant"}

            if paper_mode:
                # Paper mode : capital fictif configuré, pas d'appel exchange
                ps = self._load_paper_state()
                capital_val = ps.get("capital", config.get("trading_capital", 10000.0))
                risk_amount = capital_val * config["risk_per_trade_pct"] / 100
                risk_per_unit = abs(price - sl)
                quantity = round(risk_amount / risk_per_unit, 6) if risk_per_unit > 0 else 0
                auth = {"authorized": True, "risk_score": 0}
            else:
                account = self.exchange.get_account()
                capital_val = account.total_value

            # Use risk engine for position sizing (live only)
            if not paper_mode and config.get("use_risk_engine", True):
                sizing = self.risk_engine.calculate_position_size(
                    capital=capital_val,
                    entry_price=price,
                    stop_loss=sl,
                    signal_confidence=analysis.get("confidence", 50),
                    volatility=analysis.get("quant", {}).get("volatility_regime_pct") if analysis.get("quant") else None,
                    quant_kelly_pct=analysis.get("quant", {}).get("kelly_size") if analysis.get("quant") else None,
                )
                quantity = sizing["quantity"]

                # Risk authorization
                new_trade_data = {
                    "symbol": symbol,
                    "side": side,
                    "entry_price": price,
                    "stop_loss": sl,
                    "quantity": quantity,
                }
                auth = self.risk_engine.authorize_trade(
                    active_trades=[asdict(t) for t in self.active_trades if t.status == "OPEN"],
                    capital=capital_val,
                    new_trade=new_trade_data,
                    trade_history=self.trade_history,
                )

                if not auth["authorized"]:
                    return {
                        "status": "BLOCKED",
                        "reason": f"Risk Engine: {auth['reason']}",
                        "risk_score": auth["risk_score"],
                        "warnings": auth.get("warnings", []),
                    }
            elif not paper_mode:
                # Live : classic sizing sans risk engine
                risk_amount = capital_val * config["risk_per_trade_pct"] / 100
                risk_per_unit = abs(price - sl)
                if risk_per_unit <= 0:
                    return {"status": "SKIP", "reason": "Risk par unite invalide"}
                quantity = round(risk_amount / risk_per_unit, 6)
                cost = quantity * price
                if cost > account.cash * 0.95:
                    quantity = round((account.cash * 0.95) / price, 6)

            if quantity <= 0:
                return {"status": "SKIP", "reason": "Quantite trop faible"}

        except Exception as e:
            return {"error": f"Erreur calcul position: {e}"}

        # Execute order
        try:
            if paper_mode:
                # Paper : pas d'ordre réel, fill immédiat au prix du signal
                actual_qty = quantity
                slippage = 0.0
                order_id = f"paper-{int(datetime.now().timestamp()*1000)}"
            elif config.get("use_smart_execution", True):
                self.smart_exec.exchange = self.exchange
                urgency = config.get("execution_urgency", "normal")
                exec_result = self.smart_exec.smart_order(symbol, side, quantity, urgency)

                if exec_result.status == "FAILED":
                    return {"error": f"Execution echouee: {exec_result.orders}"}

                actual_qty = exec_result.filled_quantity
                slippage = exec_result.slippage_pct
                order_id = exec_result.orders[0].get("order_id", "") if exec_result.orders else ""
            else:
                if side == "BUY":
                    order = self.exchange.buy(symbol, quantity)
                else:
                    order = self.exchange.sell(symbol, quantity)
                actual_qty = quantity
                slippage = 0
                order_id = order.order_id or ""

            trade = ActiveTrade(
                symbol=symbol,
                side=side,
                entry_price=price,
                quantity=actual_qty,
                stop_loss=sl,
                take_profit=analysis.get("take_profit", 0),
                initial_stop_loss=sl,
                highest_price=price,
                lowest_price=price,
                order_id=order_id,
                opened_at=datetime.now().isoformat(),
                timeframes_confirmed=analysis.get("confirmations", []),
                regime=analysis.get("quant", {}).get("regime", "") if analysis.get("quant") else "",
                confidence=analysis.get("confidence", 0),
                strategies_agreed=analysis.get("quant", {}).get("strategies_agree", 0) if analysis.get("quant") else 0,
                risk_score=auth.get("risk_score", 0) if config.get("use_risk_engine") else 0,
                execution_slippage=slippage,
            )
            self.active_trades.append(trade)
            self._save_state()

            # Log de prédiction probabiliste : chaque trade devient un point de calibration.
            # On enregistre la proba estimée au moment de l'entrée pour la comparer au résultat.
            try:
                prob = analysis.get("probability", {})
                prob_up = prob.get("prob_up")
                if prob_up is None and self.prob_engine.is_ready():
                    prob_up = analysis.get("confidence", 50) / 100.0  # fallback confiance
                if prob_up is not None:
                    tp_val = analysis.get("take_profit", 0)
                    prediction_log.log_prediction(
                        symbol=symbol,
                        prob_up=prob_up if side == "BUY" else (1 - prob_up),
                        entry=price, sl=sl, tp=tp_val,
                        signal=side,
                        timeframe=config.get("timeframe", "1h"),
                    )
            except Exception:
                pass

            self.risk_engine.log_event("TRADE_OPEN", f"{side} {symbol} qty={actual_qty} @ {price}", {
                "trade": asdict(trade),
            })

            try:
                paper = config.get("paper_mode", True)
                notif.notify_trade_opened(symbol, side, price, actual_qty, sl,
                                          analysis.get("take_profit", 0), paper=paper)
            except Exception:
                pass

            return {
                "status": "EXECUTED",
                "trade": asdict(trade),
                "risk_approved": True,
                "execution_method": "smart" if config.get("use_smart_execution") else "market",
                "slippage": slippage,
            }
        except Exception as e:
            return {"error": f"Erreur ordre: {e}"}

    # ══════════════════════════════════════════════════════════
    #  MONITORING : TRAILING STOP + SL/TP
    # ══════════════════════════════════════════════════════════

    def check_stop_loss_take_profit(self) -> list:
        if not self.exchange or not self.exchange.connected:
            return []

        config = self.get_config()
        trailing_enabled = config.get("trailing_stop", False)
        trailing_pct = config.get("trailing_stop_pct", 2.0)
        trailing_activation = config.get("trailing_activation_pct", 1.0)

        closed = []
        for trade in self.active_trades:
            if trade.status != "OPEN":
                continue

            try:
                current_price = self.exchange.get_ticker_price(trade.symbol)
            except Exception:
                continue

            if trade.side == "BUY":
                trade.pnl = round((current_price - trade.entry_price) * trade.quantity, 2)
                trade.pnl_pct = round((current_price / trade.entry_price - 1) * 100, 2)

                if trailing_enabled:
                    if current_price > trade.highest_price:
                        trade.highest_price = current_price
                    profit_pct = (current_price / trade.entry_price - 1) * 100
                    if profit_pct >= trailing_activation:
                        trade.trailing_active = True
                        new_sl = trade.highest_price * (1 - trailing_pct / 100)
                        if new_sl > trade.stop_loss:
                            trade.stop_loss = round(new_sl, 4)

                if current_price <= trade.stop_loss:
                    hit = "TRAILING_SL" if trade.trailing_active else "SL_HIT"
                elif current_price >= trade.take_profit:
                    hit = "TP_HIT"
                else:
                    hit = None

            else:  # SELL
                trade.pnl = round((trade.entry_price - current_price) * trade.quantity, 2)
                trade.pnl_pct = round((1 - current_price / trade.entry_price) * 100, 2)

                if trailing_enabled:
                    if current_price < trade.lowest_price:
                        trade.lowest_price = current_price
                    profit_pct = (1 - current_price / trade.entry_price) * 100
                    if profit_pct >= trailing_activation:
                        trade.trailing_active = True
                        new_sl = trade.lowest_price * (1 + trailing_pct / 100)
                        if new_sl < trade.stop_loss:
                            trade.stop_loss = round(new_sl, 4)

                if current_price >= trade.stop_loss:
                    hit = "TRAILING_SL" if trade.trailing_active else "SL_HIT"
                elif current_price <= trade.take_profit:
                    hit = "TP_HIT"
                else:
                    hit = None

            if hit:
                if config.get("paper_mode", True):
                    # Paper : clôture simulée, mise à jour capital fictif
                    self._paper_close(trade, current_price, hit)
                else:
                    trade.status = hit
                    trade.closed_at = datetime.now().isoformat()
                    trade.close_price = current_price
                    try:
                        if config.get("use_smart_execution"):
                            self.smart_exec.exchange = self.exchange
                            close_side = "SELL" if trade.side == "BUY" else "BUY"
                            self.smart_exec.smart_order(trade.symbol, close_side, trade.quantity, "high")
                        else:
                            if trade.side == "BUY":
                                self.exchange.sell(trade.symbol, trade.quantity)
                            else:
                                self.exchange.buy(trade.symbol, trade.quantity)
                    except Exception:
                        pass

                self.trade_history.append(asdict(trade))
                closed.append(asdict(trade))

                self.risk_engine.log_event("TRADE_CLOSE", f"{hit} {trade.symbol} PnL={trade.pnl}", {
                    "trade": asdict(trade),
                })

                try:
                    reason_label = {"SL_HIT": "Stop Loss", "TP_HIT": "Take Profit",
                                    "TRAILING_SL": "Trailing Stop"}.get(hit, hit)
                    paper = config.get("paper_mode", True)
                    notif.notify_trade_closed(trade.symbol, trade.side,
                                              trade.pnl or 0, trade.pnl_pct or 0,
                                              reason_label, paper=paper)
                except Exception:
                    pass

        self.active_trades = [t for t in self.active_trades if t.status == "OPEN"]
        self._save_state()
        return closed

    def close_trade(self, symbol: str) -> dict:
        cfg = self.get_config()
        for trade in self.active_trades:
            if trade.symbol == symbol and trade.status == "OPEN":
                try:
                    current_price = self.exchange.get_ticker_price(symbol)
                    if cfg.get("paper_mode", True):
                        # Paper : clôture simulée
                        self._paper_close(trade, current_price, "MANUAL_CLOSE")
                    else:
                        if trade.side == "BUY":
                            self.exchange.sell(symbol, trade.quantity)
                            trade.pnl = round((current_price - trade.entry_price) * trade.quantity, 2)
                        else:
                            self.exchange.buy(symbol, trade.quantity)
                            trade.pnl = round((trade.entry_price - current_price) * trade.quantity, 2)
                        trade.pnl_pct = round(trade.pnl / (trade.entry_price * trade.quantity) * 100, 2)
                        trade.close_price = current_price
                except Exception as e:
                    return {"error": str(e)}

                if not cfg.get("paper_mode", True):
                    # Live : status pas encore mis par _paper_close
                    trade.status = "MANUAL_CLOSE"
                    trade.closed_at = datetime.now().isoformat()
                self.trade_history.append(asdict(trade))
                self.active_trades = [t for t in self.active_trades if t.status == "OPEN"]
                self._save_state()

                try:
                    paper = cfg.get("paper_mode", True)
                    notif.notify_trade_closed(trade.symbol, trade.side,
                                              trade.pnl or 0, trade.pnl_pct or 0,
                                              "Fermeture manuelle", paper=paper)
                except Exception:
                    pass

                return {"status": "CLOSED", "trade": asdict(trade)}

        return {"error": f"Aucun trade ouvert sur {symbol}"}

    # ══════════════════════════════════════════════════════════
    #  RISK DASHBOARD
    # ══════════════════════════════════════════════════════════

    def get_risk_dashboard(self) -> dict:
        """Tableau de bord risque complet."""
        if not self.exchange or not self.exchange.connected:
            return {"error": "Exchange non connecte"}

        try:
            account = self.exchange.get_account()
            capital = account.total_value

            active = [asdict(t) for t in self.active_trades if t.status == "OPEN"]
            risk = self.risk_engine.assess_portfolio_risk(active, capital, self.trade_history)

            # Correlation matrix
            price_data = {}
            for trade in self.active_trades:
                if trade.status == "OPEN":
                    try:
                        df = self._fetch_ohlcv(trade.symbol, "1h", 50)
                        price_data[trade.symbol] = df["close"].tolist()
                    except Exception:
                        pass

            correlation = self.risk_engine.calculate_correlation_matrix(price_data) if len(price_data) > 1 else None

            return {
                "capital": round(capital, 2),
                "risk_metrics": asdict(risk),
                "correlation": correlation,
                "risk_config": self.risk_engine.config,
                "risk_log": self.risk_engine.risk_log[-20:],
            }
        except Exception as e:
            return {"error": str(e)}

    # ══════════════════════════════════════════════════════════
    #  PERFORMANCE METRICS
    # ══════════════════════════════════════════════════════════

    def get_performance(self) -> dict:
        history = self.trade_history
        if not history:
            return {
                "total_trades": 0, "win_rate": 0, "total_pnl": 0,
                "avg_pnl": 0, "best_trade": 0, "worst_trade": 0,
                "profit_factor": 0, "avg_win": 0, "avg_loss": 0,
                "max_consecutive_wins": 0, "max_consecutive_losses": 0,
                "pnl_history": [], "monthly_pnl": {}, "by_symbol": {},
                "sharpe_ratio": 0, "sortino_ratio": 0, "calmar_ratio": 0,
                "expectancy": 0, "max_drawdown": 0,
                "daily_pnl": 0, "daily_trades": 0,
            }

        pnls = [t.get("pnl", 0) for t in history]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        total_wins = sum(wins) if wins else 0
        total_losses = abs(sum(losses)) if losses else 0

        # Consecutive
        max_cw, max_cl, cw, cl = 0, 0, 0, 0
        for p in pnls:
            if p > 0:
                cw += 1; cl = 0; max_cw = max(max_cw, cw)
            elif p < 0:
                cl += 1; cw = 0; max_cl = max(max_cl, cl)

        # PnL cumule
        cumulative = []
        running = 0
        for t in history:
            running += t.get("pnl", 0)
            cumulative.append({"date": t.get("closed_at", ""), "pnl": round(running, 2), "symbol": t.get("symbol", "")})

        # Monthly
        monthly = {}
        for t in history:
            ds = t.get("closed_at", "")
            if ds:
                month = ds[:7]
                monthly[month] = round(monthly.get(month, 0) + t.get("pnl", 0), 2)

        # By symbol
        by_symbol = {}
        for t in history:
            sym = t.get("symbol", "?")
            if sym not in by_symbol:
                by_symbol[sym] = {"trades": 0, "wins": 0, "pnl": 0}
            by_symbol[sym]["trades"] += 1
            if t.get("pnl", 0) > 0:
                by_symbol[sym]["wins"] += 1
            by_symbol[sym]["pnl"] = round(by_symbol[sym]["pnl"] + t.get("pnl", 0), 2)

        # Advanced ratios
        import numpy as np
        pnl_array = np.array(pnls) if pnls else np.array([0])
        avg_pnl = np.mean(pnl_array)
        std_pnl = np.std(pnl_array)

        # Sharpe (annualized, assuming ~1 trade/day)
        sharpe = (avg_pnl / std_pnl * np.sqrt(252)) if std_pnl > 0 else 0

        # Sortino (downside deviation only)
        downside = pnl_array[pnl_array < 0]
        downside_std = np.std(downside) if len(downside) > 0 else 1
        sortino = (avg_pnl / downside_std * np.sqrt(252)) if downside_std > 0 else 0

        # Calmar (return / max drawdown)
        cum = np.cumsum(pnl_array)
        peak = np.maximum.accumulate(cum)
        dd = cum - peak
        max_dd = abs(np.min(dd)) if len(dd) > 0 else 1
        calmar = (sum(pnls) / max_dd) if max_dd > 0 else 0

        # Expectancy
        win_rate = len(wins) / len(history) if history else 0
        avg_win_val = total_wins / len(wins) if wins else 0
        avg_loss_val = total_losses / len(losses) if losses else 0
        expectancy = (win_rate * avg_win_val - (1 - win_rate) * avg_loss_val)

        # PnL du jour (trades clotures aujourd'hui)
        today_str = datetime.now().strftime("%Y-%m-%d")
        daily_pnl = round(sum(
            t.get("pnl", 0) for t in history
            if (t.get("closed_at") or "")[:10] == today_str
        ), 2)
        daily_trades = sum(
            1 for t in history
            if (t.get("closed_at") or "")[:10] == today_str
        )

        return {
            "total_trades": len(history),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate * 100, 1),
            "total_pnl": round(sum(pnls), 2),
            "avg_pnl": round(avg_pnl, 2),
            "best_trade": round(max(pnls), 2) if pnls else 0,
            "worst_trade": round(min(pnls), 2) if pnls else 0,
            "profit_factor": round(total_wins / total_losses, 2) if total_losses > 0 else 0,
            "avg_win": round(avg_win_val, 2),
            "avg_loss": round(-avg_loss_val, 2),
            "max_consecutive_wins": max_cw,
            "max_consecutive_losses": max_cl,
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2),
            "calmar_ratio": round(calmar, 2),
            "expectancy": round(expectancy, 2),
            "max_drawdown": round(max_dd, 2),
            "daily_pnl": daily_pnl,
            "daily_trades": daily_trades,
            "pnl_history": cumulative,
            "monthly_pnl": monthly,
            "by_symbol": by_symbol,
        }

    # ══════════════════════════════════════════════════════════
    #  BOUCLE AUTOMATIQUE
    # ══════════════════════════════════════════════════════════

    def _models_mtime(self) -> float:
        """mtime le plus récent des fichiers modèle persistés (0.0 si aucun)."""
        ts = 0.0
        for eng in (self.prob_engine, self.mh_engine):
            try:
                p = eng._path()
                if os.path.exists(p):
                    ts = max(ts, os.path.getmtime(p))
            except Exception:
                pass
        return ts

    def _log_shadow_prediction(self, analysis: dict, config: dict):
        """
        Logue une prédiction "fantôme" : l'estimation du modèle pour ce setup, SANS
        ouvrir de trade. Permet d'accumuler de la calibration même quand les filtres
        de trading bloquent l'exécution (sinon 0 trade = 0 donnée de validation).
        Dédup par symbole (une seule en attente) ; si un vrai trade vient d'être
        ouvert, la dédup ignore ce doublon.
        """
        try:
            prob = analysis.get("probability", {})
            prob_up = prob.get("prob_up") if isinstance(prob, dict) else None
            if prob_up is None:
                return
            entry = analysis.get("price", 0)
            sl = analysis.get("stop_loss", 0)
            tp = analysis.get("take_profit", 0)
            if not (entry and sl and tp):
                return
            signal = analysis.get("signal", analysis.get("final_signal", "HOLD"))
            side = "BUY" if "BUY" in signal else "SELL"
            prediction_log.log_prediction(
                symbol=analysis.get("symbol", ""),
                prob_up=prob_up if side == "BUY" else 1 - prob_up,
                entry=entry, sl=sl, tp=tp, signal=side,
                timeframe=config.get("timeframe", "1h"),
                shadow=True, dedup=True,
            )
        except Exception:
            pass

    def _auto_train_models(self, config: dict):
        """
        Auto-entraine les modeles probabilistes s'ils sont absents ou perimes.
        Tourne en tache de fond (sur Railway l'exchange est connecte en permanence),
        ce qui active le ML sans intervention manuelle. Entrainement rapide (numpy
        pur, ~1-2s) donc ne bloque pas le trading de facon sensible.
        """
        if not config.get("auto_train_models", True):
            return
        if not self.exchange or not self.exchange.connected:
            return  # besoin de l'historique via l'exchange

        now = time.time()
        refresh_secs = config.get("model_refresh_days", 7) * 86400
        # Au demarrage (_last_model_train_ts=0), deduire la date du dernier
        # entrainement du mtime des fichiers modele persistes. Sinon chaque
        # redeploy Railway reentrainerait tout inutilement alors que les modeles
        # sur le volume sont frais.
        if self._last_model_train_ts == 0.0:
            self._last_model_train_ts = self._models_mtime()
        prob_ready = self.prob_engine.is_ready()
        mh_ready = self.mh_engine.is_ready()
        stale = (now - self._last_model_train_ts) > refresh_secs

        # Rien a faire si tout est pret et pas perime
        if prob_ready and mh_ready and not stale:
            return

        # Symbole d'entrainement : 1er de la watchlist (base seule, _fetch_ohlcv
        # ajoute lui-meme /USDT).
        symbols = config.get("symbols", ["BTC"])
        base = (symbols[0] if symbols else "BTC").split("/")[0]
        label = base + "/USDT"
        timeframe = config.get("timeframe", "1h")
        sl_mult = config.get("sl_atr_multiplier", 1.5)
        tp_mult = config.get("tp_atr_multiplier", 3.0)

        try:
            df = self._fetch_ohlcv(base, timeframe, limit=1050)
            if len(df) < 200:
                return
            if not prob_ready or stale:
                r = self.prob_engine.train_from_history(df, label, sl_mult=sl_mult, tp_mult=tp_mult)
                if not r.get("error"):
                    print(f"[AutoTrader] Auto-train proba {label}: "
                          f"{r.get('n_samples')} ech, Brier {r.get('test_brier')}")
            if not mh_ready or stale:
                r2 = self.mh_engine.train_from_history(df, label, timeframe=timeframe)
                if not r2.get("error"):
                    ok = len([h for h in r2.get("horizons", []) if not h.get("error")])
                    print(f"[AutoTrader] Auto-train multi-horizon {label}: {ok} horizons")
            self._last_model_train_ts = now
        except Exception as e:
            print(f"[AutoTrader] auto-train error: {e}")

    def _trading_loop(self):
        scan_counter = 0
        last_daily_summary_date = None
        last_heartbeat_hour = -1   # heure (0-23) du dernier heartbeat
        loop_start_time = time.time()
        last_regime_per_symbol: dict = {}  # symbol -> regime str
        while self.running:
            config = self.get_config()
            if not config["enabled"]:
                time.sleep(10)
                continue

            try:
                # 0. Periodic market scan (every 3 cycles)
                if config.get("use_market_scanner") and scan_counter % 3 == 0:
                    try:
                        self.scanner.exchange = self.exchange
                        self.scanner.full_scan()
                        results = self.scanner.cache.get("results", [])
                        print(f"[AutoTrader] Market scan complete - {len(results)} opportunities")
                        # Notifier le meilleur pick si score >= 70
                        if results:
                            top = max(results, key=lambda x: x.get("score", 0))
                            if top.get("score", 0) >= 70:
                                try:
                                    notif.notify_smart_pick(top)
                                except Exception:
                                    pass
                    except Exception as e:
                        print(f"[AutoTrader] Scan error: {e}")
                scan_counter += 1

                # 1. Check SL/TP/trailing
                self.check_stop_loss_take_profit()

                # 1b. Réconcilie les prédictions probabilistes en attente
                # (compare proba estimée vs résultat réel → calibration continue)
                try:
                    prediction_log.reconcile(
                        lambda s: self.exchange.get_ticker_price(s)
                        if self.exchange and self.exchange.connected else None
                    )
                except Exception:
                    pass

                # 1c. Auto-entraine les modeles ML si absents ou perimes
                # (active le ML sans intervention manuelle ; la methode se garde
                # elle-meme contre les re-entrainements inutiles)
                self._auto_train_models(config)

                # Limite de perte journalière - auto kill-switch
                max_daily_loss = config.get("max_daily_loss_pct", 0)
                if max_daily_loss > 0:
                    try:
                        perf = self.get_performance()
                        daily_pnl = perf.get("daily_pnl", 0)
                        capital = config.get("trading_capital", 10000)
                        if daily_pnl < 0 and capital > 0:
                            daily_loss_pct = abs(daily_pnl) / capital * 100
                            if daily_loss_pct >= max_daily_loss:
                                from settings import trigger_kill_switch
                                reason = (
                                    f"Limite perte journalière dépassée : "
                                    f"-{daily_loss_pct:.1f}% ({daily_pnl:+.2f}$)"
                                )
                                trigger_kill_switch(reason)
                                print(f"[AutoTrader] AUTO KILL-SWITCH: {reason}")
                                self.running = False
                    except Exception as e:
                        print(f"[AutoTrader] daily loss check error: {e}")

                # 2. Analyze all symbols (includes scanner results if enabled)
                analyses = self.analyze_all()

                for analysis in analyses:
                    if analysis.get("error"):
                        continue

                    # Changement de régime de marché
                    sym = analysis.get("symbol", "")
                    new_regime = (analysis.get("quant") or {}).get("regime", "")
                    if new_regime and sym:
                        old_regime = last_regime_per_symbol.get(sym, "")
                        if old_regime and old_regime != new_regime:
                            try:
                                notif.notify_regime_change(old_regime, new_regime, sym)
                            except Exception:
                                pass
                        last_regime_per_symbol[sym] = new_regime

                    signal = analysis.get("signal", analysis.get("final_signal", "HOLD"))
                    if signal in ("STRONG_BUY", "BUY", "STRONG_SELL", "SELL"):
                        result = self.execute_signal(analysis)
                        if result.get("status") == "BLOCKED":
                            print(f"[AutoTrader] RISK BLOCK: {result.get('reason')}")
                        elif result.get("status") == "SKIP":
                            # Signal fort mais trade skipped - notifier si STRONG
                            if signal in ("STRONG_BUY", "STRONG_SELL"):
                                conf = analysis.get("confidence", 0)
                                price = analysis.get("price", 0)
                                try:
                                    arrow = "🚀" if "BUY" in signal else "📉"
                                    notif.notify(
                                        f"{arrow} <b>Signal {signal}</b> - {sym}\n"
                                        f"Prix : {price} · Confiance : {conf}%\n"
                                        f"<i>Non exécuté : {result.get('reason', '')}</i>",
                                        "trades"
                                    )
                                except Exception:
                                    pass
                        # Prédiction fantôme : calibration même sans trade exécuté
                        # (dédup ignore le doublon si un vrai trade vient d'ouvrir).
                        self._log_shadow_prediction(analysis, config)

            except Exception as e:
                print(f"[AutoTrader] Erreur: {e}")
                self.risk_engine.log_event("ERROR", str(e))
                try:
                    notif.notify_error(str(e), "AutoTrader._trading_loop")
                except Exception:
                    pass

            # Résumé quotidien à minuit
            today = datetime.now().date()
            if last_daily_summary_date != today and datetime.now().hour == 0:
                last_daily_summary_date = today
                try:
                    perf = self.get_performance()
                    notif.notify_daily_summary({
                        "total_pnl": perf.get("daily_pnl", 0),
                        "total_trades": perf.get("total_trades", 0),
                        "win_rate": perf.get("win_rate", 0),
                    })
                except Exception:
                    pass

            # Heartbeat toutes les 6h (heures paires : 0, 6, 12, 18)
            current_hour = datetime.now().hour
            if current_hour % 6 == 0 and current_hour != last_heartbeat_hour:
                last_heartbeat_hour = current_hour
                try:
                    perf = self.get_performance()
                    uptime_h = (time.time() - loop_start_time) / 3600
                    notif.notify_heartbeat({
                        "active_trades": len(self.active_trades),
                        "total_pnl": perf.get("total_pnl", 0),
                        "daily_pnl": perf.get("daily_pnl", 0),
                        "win_rate": perf.get("win_rate", 0),
                        "uptime_hours": uptime_h,
                    })
                except Exception:
                    pass

            time.sleep(config.get("check_interval", 300))

    def start(self):
        if self.running:
            return {"status": "already_running"}
        self.running = True
        self._thread = threading.Thread(target=self._trading_loop, daemon=True)
        self._thread.start()
        return {"status": "started"}

    def stop(self):
        self.running = False
        return {"status": "stopped"}
