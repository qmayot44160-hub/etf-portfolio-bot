"""
Auto-trader — surveille les cours, passe des ordres, gère SL/TP/Trailing.

Boucle de trading :
1. Récupère les données OHLCV sur plusieurs timeframes
2. Analyse technique multi-timeframe → signal confirmé
3. Si signal BUY/SELL → place un ordre avec SL, TP et trailing stop
4. Surveille les positions ouvertes → SL/TP/trailing → ferme
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


TRADES_FILE = "trades_history.json"
ACTIVE_TRADES_FILE = "active_trades.json"
TRADER_CONFIG_FILE = "trader_config.json"


@dataclass
class ActiveTrade:
    symbol: str
    side: str  # "BUY" ou "SELL"
    entry_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    initial_stop_loss: float = 0
    highest_price: float = 0      # pour trailing stop (BUY)
    lowest_price: float = 999999  # pour trailing stop (SELL)
    trailing_active: bool = False
    order_id: str = ""
    opened_at: str = ""
    status: str = "OPEN"
    pnl: float = 0
    pnl_pct: float = 0
    closed_at: str = ""
    close_price: float = 0
    timeframes_confirmed: list = field(default_factory=list)


class AutoTrader:
    def __init__(self, exchange=None):
        self.exchange = exchange
        self.strategy = TradingStrategy()
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
                    # Handle missing fields for backward compat
                    for k in ActiveTrade.__dataclass_fields__:
                        if k not in t:
                            t[k] = ActiveTrade.__dataclass_fields__[k].default \
                                if ActiveTrade.__dataclass_fields__[k].default is not dataclass \
                                else ActiveTrade.__dataclass_fields__[k].default_factory()
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
        }

    def save_config(self, config: dict):
        with open(TRADER_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)

    def get_status(self) -> dict:
        return {
            "running": self.running,
            "active_trades": len(self.active_trades),
            "total_trades": len(self.trade_history),
            "config": self.get_config(),
        }

    def get_active_trades(self) -> list:
        # Update P&L en temps réel
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

    # ── Analyse multi-timeframe ──

    def _fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100):
        """Récupère les données OHLCV."""
        import pandas as pd
        pair = f"{symbol}/USDT"
        ohlcv = self.exchange.exchange.fetch_ohlcv(pair, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df

    def analyze_symbol(self, symbol: str, timeframe: str = "1h") -> dict:
        """Analyse technique d'un symbole."""
        if not self.exchange or not self.exchange.connected:
            return {"symbol": symbol, "error": "Exchange non connecté"}

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

    def analyze_multi_timeframe(self, symbol: str) -> dict:
        """
        Analyse sur plusieurs timeframes et confirme le signal.
        Le signal principal vient du timeframe configuré.
        Les timeframes supérieurs confirment ou invalident.
        """
        if not self.exchange or not self.exchange.connected:
            return {"symbol": symbol, "error": "Exchange non connecté"}

        config = self.get_config()
        timeframes = config.get("confirm_timeframes", ["1h", "4h"])

        results = {}
        for tf in timeframes:
            try:
                results[tf] = self.analyze_symbol(symbol, tf)
            except Exception as e:
                results[tf] = {"symbol": symbol, "timeframe": tf, "error": str(e)}

        # Signal principal = premier timeframe
        primary_tf = timeframes[0]
        primary = results.get(primary_tf, {})
        if primary.get("error"):
            return primary

        primary_signal = primary.get("signal", "HOLD")

        # Confirmation : les autres timeframes doivent aller dans le même sens
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
            confirmations.append({
                "timeframe": tf,
                "signal": tf_signal,
                "confirms": agrees,
            })

        all_confirmed = all(c["confirms"] for c in confirmations)

        # Ajuster la confiance selon la confirmation
        confidence = primary.get("confidence", 0)
        if all_confirmed and len(confirmations) > 0:
            confidence = min(confidence + 20, 100)
        elif not all_confirmed:
            confidence = max(confidence - 30, 0)
            if config.get("only_strong_signals"):
                primary_signal = "HOLD"  # Annule le signal si pas confirmé

        final = {
            **primary,
            "signal": primary_signal if all_confirmed or not config.get("only_strong_signals") else "HOLD",
            "confidence": round(confidence, 1),
            "multi_timeframe": True,
            "confirmations": confirmations,
            "all_confirmed": all_confirmed,
        }
        return final

    def analyze_all(self) -> list:
        """Analyse tous les symboles avec multi-timeframe."""
        config = self.get_config()
        results = []
        for symbol in config["symbols"]:
            if len(config.get("confirm_timeframes", [])) > 1:
                result = self.analyze_multi_timeframe(symbol)
            else:
                result = self.analyze_symbol(symbol, config["timeframe"])
            results.append(result)
        return results

    # ── Exécution ──

    def execute_signal(self, analysis: dict) -> dict:
        if not self.exchange or not self.exchange.connected:
            return {"error": "Exchange non connecté"}

        config = self.get_config()
        symbol = analysis["symbol"]
        signal = analysis["signal"]

        existing = [t for t in self.active_trades if t.symbol == symbol and t.status == "OPEN"]
        if existing:
            return {"status": "SKIP", "reason": f"Trade déjà ouvert sur {symbol}"}

        open_count = len([t for t in self.active_trades if t.status == "OPEN"])
        if open_count >= config["max_open_trades"]:
            return {"status": "SKIP", "reason": f"Max trades ouverts ({config['max_open_trades']})"}

        if signal not in ("STRONG_BUY", "BUY", "STRONG_SELL", "SELL"):
            return {"status": "SKIP", "reason": "Signal HOLD — pas d'action"}

        # Position sizing
        try:
            account = self.exchange.get_account()
            risk_amount = account.total_value * config["risk_per_trade_pct"] / 100
            price = analysis["price"]
            sl = analysis["stop_loss"]
            risk_per_unit = abs(price - sl)

            if risk_per_unit <= 0:
                return {"status": "SKIP", "reason": "Risk par unité invalide"}

            quantity = round(risk_amount / risk_per_unit, 6)
            cost = quantity * price

            if cost > account.cash * 0.95:
                quantity = round((account.cash * 0.95) / price, 6)

            if quantity <= 0:
                return {"status": "SKIP", "reason": "Quantité trop faible"}

        except Exception as e:
            return {"error": f"Erreur calcul position: {e}"}

        # Passer l'ordre
        try:
            if signal in ("STRONG_BUY", "BUY"):
                order = self.exchange.buy(symbol, quantity)
                side = "BUY"
            else:
                order = self.exchange.sell(symbol, quantity)
                side = "SELL"

            trade = ActiveTrade(
                symbol=symbol,
                side=side,
                entry_price=price,
                quantity=quantity,
                stop_loss=sl,
                take_profit=analysis["take_profit"],
                initial_stop_loss=sl,
                highest_price=price,
                lowest_price=price,
                order_id=order.order_id or "",
                opened_at=datetime.now().isoformat(),
                timeframes_confirmed=analysis.get("confirmations", []),
            )
            self.active_trades.append(trade)
            self._save_state()

            return {
                "status": "EXECUTED",
                "trade": asdict(trade),
                "order_status": order.status,
            }
        except Exception as e:
            return {"error": f"Erreur ordre: {e}"}

    # ── Trailing Stop + SL/TP Monitoring ──

    def check_stop_loss_take_profit(self) -> list:
        """Vérifie SL/TP/Trailing de tous les trades actifs."""
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

            # Update P&L
            if trade.side == "BUY":
                trade.pnl = round((current_price - trade.entry_price) * trade.quantity, 2)
                trade.pnl_pct = round((current_price / trade.entry_price - 1) * 100, 2)

                # Trailing stop logic
                if trailing_enabled:
                    if current_price > trade.highest_price:
                        trade.highest_price = current_price

                    # Active le trailing quand le profit atteint le seuil
                    profit_pct = (current_price / trade.entry_price - 1) * 100
                    if profit_pct >= trailing_activation:
                        trade.trailing_active = True
                        new_sl = trade.highest_price * (1 - trailing_pct / 100)
                        if new_sl > trade.stop_loss:
                            trade.stop_loss = round(new_sl, 4)

                # Check SL/TP
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
                    if trade.side == "BUY":
                        self.exchange.sell(trade.symbol, trade.quantity)
                    else:
                        self.exchange.buy(trade.symbol, trade.quantity)
                except Exception:
                    pass

                self.trade_history.append(asdict(trade))
                closed.append(asdict(trade))

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
                return {"status": "CLOSED", "trade": asdict(trade)}

        return {"error": f"Aucun trade ouvert sur {symbol}"}

    # ── Performance metrics ──

    def get_performance(self) -> dict:
        """Calcule les métriques de performance globales."""
        history = self.trade_history
        if not history:
            return {
                "total_trades": 0, "win_rate": 0, "total_pnl": 0,
                "avg_pnl": 0, "best_trade": 0, "worst_trade": 0,
                "profit_factor": 0, "avg_win": 0, "avg_loss": 0,
                "max_consecutive_wins": 0, "max_consecutive_losses": 0,
                "pnl_history": [], "monthly_pnl": {},
            }

        pnls = [t.get("pnl", 0) for t in history]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        total_wins = sum(wins) if wins else 0
        total_losses = abs(sum(losses)) if losses else 0

        # Consecutive wins/losses
        max_consec_wins = 0
        max_consec_losses = 0
        current_wins = 0
        current_losses = 0
        for p in pnls:
            if p > 0:
                current_wins += 1
                current_losses = 0
                max_consec_wins = max(max_consec_wins, current_wins)
            elif p < 0:
                current_losses += 1
                current_wins = 0
                max_consec_losses = max(max_consec_losses, current_losses)

        # PnL cumulé
        cumulative = []
        running = 0
        for t in history:
            running += t.get("pnl", 0)
            cumulative.append({
                "date": t.get("closed_at", ""),
                "pnl": round(running, 2),
                "symbol": t.get("symbol", ""),
            })

        # PnL mensuel
        monthly = {}
        for t in history:
            date_str = t.get("closed_at", "")
            if date_str:
                month = date_str[:7]  # YYYY-MM
                monthly[month] = round(monthly.get(month, 0) + t.get("pnl", 0), 2)

        # Par symbole
        by_symbol = {}
        for t in history:
            sym = t.get("symbol", "?")
            if sym not in by_symbol:
                by_symbol[sym] = {"trades": 0, "wins": 0, "pnl": 0}
            by_symbol[sym]["trades"] += 1
            if t.get("pnl", 0) > 0:
                by_symbol[sym]["wins"] += 1
            by_symbol[sym]["pnl"] = round(by_symbol[sym]["pnl"] + t.get("pnl", 0), 2)

        return {
            "total_trades": len(history),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(history) * 100, 1) if history else 0,
            "total_pnl": round(sum(pnls), 2),
            "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0,
            "best_trade": round(max(pnls), 2) if pnls else 0,
            "worst_trade": round(min(pnls), 2) if pnls else 0,
            "profit_factor": round(total_wins / total_losses, 2) if total_losses > 0 else 0,
            "avg_win": round(total_wins / len(wins), 2) if wins else 0,
            "avg_loss": round(-total_losses / len(losses), 2) if losses else 0,
            "max_consecutive_wins": max_consec_wins,
            "max_consecutive_losses": max_consec_losses,
            "pnl_history": cumulative,
            "monthly_pnl": monthly,
            "by_symbol": by_symbol,
        }

    # ── Boucle automatique ──

    def _trading_loop(self):
        while self.running:
            config = self.get_config()
            if not config["enabled"]:
                time.sleep(10)
                continue

            try:
                self.check_stop_loss_take_profit()

                analyses = self.analyze_all()
                for analysis in analyses:
                    if analysis.get("error"):
                        continue
                    signal = analysis.get("signal", "HOLD")
                    if signal in ("STRONG_BUY", "BUY", "STRONG_SELL", "SELL"):
                        self.execute_signal(analysis)

            except Exception as e:
                print(f"[AutoTrader] Erreur: {e}")

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
