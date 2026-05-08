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
from dataclasses import dataclass, asdict, field
from typing import Optional
from config import CRYPTO_PORTFOLIO
from trading_strategy import TradingStrategy, Signal
from quant_models import QuantModels, MarketRegime
from risk_engine import RiskEngine
from smart_execution import SmartExecution
from market_intelligence import MarketIntelligence
from market_scanner import MarketScanner


from data_paths import data_path
import notifications as notif

TRADES_FILE = data_path("trades_history.json")
ACTIVE_TRADES_FILE = data_path("active_trades.json")
TRADER_CONFIG_FILE = data_path("trader_config.json")


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
        self.active_trades: list[ActiveTrade] = []
        self.trade_history: list = []
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._load_state()

    def _load_state(self):
        if os.path.exists(ACTIVE_TRADES_FILE):
            with open(ACTIVE_TRADES_FILE, "r") as f:
                data = json.load(f)
                self.active_trades = []
                for t in data:
                    for k in ActiveTrade.__dataclass_fields__:
                        if k not in t:
                            df = ActiveTrade.__dataclass_fields__[k]
                            t[k] = df.default if df.default is not dataclass else df.default_factory()
                    self.active_trades.append(ActiveTrade(**{
                        k: v for k, v in t.items()
                        if k in ActiveTrade.__dataclass_fields__
                    }))
        if os.path.exists(TRADES_FILE):
            with open(TRADES_FILE, "r") as f:
                self.trade_history = json.load(f)

    def _save_state(self):
        with open(ACTIVE_TRADES_FILE, "w") as f:
            json.dump([asdict(t) for t in self.active_trades], f, indent=2)
        with open(TRADES_FILE, "w") as f:
            json.dump(self.trade_history[-500:], f, indent=2)

    def get_config(self) -> dict:
        if os.path.exists(TRADER_CONFIG_FILE):
            with open(TRADER_CONFIG_FILE, "r") as f:
                return json.load(f)
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
            "min_confidence": 40,
            "min_strategies_agree": 2,
            "execution_urgency": "normal",  # low, normal, high
        }

    def save_config(self, config: dict):
        with open(TRADER_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)

    def get_status(self) -> dict:
        live = bool(self.exchange and self.exchange.connected)
        return {
            "running": self.running,
            "live_mode": live,
            "mode": "LIVE" if live else "SIMULATION",
            "active_trades": len(self.active_trades),
            "total_trades": len(self.trade_history),
            "config": self.get_config(),
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
        pair = f"{symbol}/USDT"
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
            return {
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
            reasons.append(f"AT: HOLD")

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
        try:
            account = self.exchange.get_account()
            price = analysis.get("price", 0)
            sl = analysis.get("stop_loss", 0)

            if not price or not sl:
                return {"status": "SKIP", "reason": "Prix ou SL manquant"}

            # Use risk engine for position sizing
            if config.get("use_risk_engine", True):
                sizing = self.risk_engine.calculate_position_size(
                    capital=account.total_value,
                    entry_price=price,
                    stop_loss=sl,
                    signal_confidence=analysis.get("confidence", 50),
                    volatility=analysis.get("quant", {}).get("volatility_regime_pct") if analysis.get("quant") else None,
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
                    capital=account.total_value,
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
            else:
                # Classic sizing
                risk_amount = account.total_value * config["risk_per_trade_pct"] / 100
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
            if config.get("use_smart_execution", True):
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
                    paper = self.get_config().get("paper_mode", True)
                    notif.notify_trade_closed(trade.symbol, trade.side,
                                              trade.pnl or 0, trade.pnl_pct or 0,
                                              reason_label, paper=paper)
                except Exception:
                    pass

        self.active_trades = [t for t in self.active_trades if t.status == "OPEN"]
        self._save_state()
        return closed

    def close_trade(self, symbol: str) -> dict:
        for trade in self.active_trades:
            if trade.symbol == symbol and trade.status == "OPEN":
                try:
                    current_price = self.exchange.get_ticker_price(symbol)
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

                trade.status = "MANUAL_CLOSE"
                trade.closed_at = datetime.now().isoformat()
                self.trade_history.append(asdict(trade))
                self.active_trades = [t for t in self.active_trades if t.status == "OPEN"]
                self._save_state()

                try:
                    paper = self.get_config().get("paper_mode", True)
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
                "expectancy": 0,
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
            "pnl_history": cumulative,
            "monthly_pnl": monthly,
            "by_symbol": by_symbol,
        }

    # ══════════════════════════════════════════════════════════
    #  BOUCLE AUTOMATIQUE
    # ══════════════════════════════════════════════════════════

    def _trading_loop(self):
        scan_counter = 0
        last_daily_summary_date = None
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
                        print(f"[AutoTrader] Market scan complete - {len(self.scanner.cache.get('results', []))} opportunities")
                    except Exception as e:
                        print(f"[AutoTrader] Scan error: {e}")
                scan_counter += 1

                # 1. Check SL/TP/trailing
                self.check_stop_loss_take_profit()

                # 2. Analyze all symbols (includes scanner results if enabled)
                analyses = self.analyze_all()

                for analysis in analyses:
                    if analysis.get("error"):
                        continue
                    signal = analysis.get("signal", analysis.get("final_signal", "HOLD"))
                    if signal in ("STRONG_BUY", "BUY", "STRONG_SELL", "SELL"):
                        result = self.execute_signal(analysis)
                        if result.get("status") == "BLOCKED":
                            print(f"[AutoTrader] RISK BLOCK: {result.get('reason')}")
                        elif result.get("status") == "SKIP":
                            # Signal fort mais trade skipped - notifier si STRONG
                            if signal in ("STRONG_BUY", "STRONG_SELL"):
                                sym = analysis.get("symbol", "")
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
