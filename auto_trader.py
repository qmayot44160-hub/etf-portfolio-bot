"""
Auto-trader — surveille les cours, passe des ordres, gère SL/TP.

Boucle de trading :
1. Récupère les données OHLCV
2. Analyse technique → signal
3. Si signal BUY/SELL → place un ordre avec SL et TP
4. Surveille les positions ouvertes → SL/TP hit → ferme
"""

import json
import os
import time
import threading
from datetime import datetime
from dataclasses import dataclass, asdict
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
    order_id: str = ""
    opened_at: str = ""
    status: str = "OPEN"  # OPEN, SL_HIT, TP_HIT, MANUAL_CLOSE
    pnl: float = 0
    closed_at: str = ""


class AutoTrader:
    def __init__(self, exchange=None):
        self.exchange = exchange  # broker MEXC
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
                self.active_trades = [ActiveTrade(**t) for t in data]
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
            "check_interval": 300,  # secondes
            "max_open_trades": 5,
            "risk_per_trade_pct": 2.0,
            "sl_atr_multiplier": 1.5,
            "tp_atr_multiplier": 3.0,
            "trailing_stop": False,
            "trailing_stop_pct": 2.0,
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
        return [asdict(t) for t in self.active_trades]

    def get_trade_history(self) -> list:
        return self.trade_history[-50:]

    # ── Analyse ──

    def analyze_symbol(self, symbol: str, timeframe: str = "1h") -> dict:
        """Analyse technique d'un symbole."""
        if not self.exchange or not self.exchange.connected:
            return {"symbol": symbol, "error": "Exchange non connecté"}

        try:
            pair = f"{symbol}/USDT"
            ohlcv = self.exchange.exchange.fetch_ohlcv(pair, timeframe, limit=100)

            import pandas as pd
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

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

    def analyze_all(self) -> list:
        """Analyse tous les symboles configurés."""
        config = self.get_config()
        results = []
        for symbol in config["symbols"]:
            result = self.analyze_symbol(symbol, config["timeframe"])
            results.append(result)
        return results

    # ── Exécution ──

    def execute_signal(self, analysis: dict) -> dict:
        """Exécute un trade basé sur l'analyse."""
        if not self.exchange or not self.exchange.connected:
            return {"error": "Exchange non connecté"}

        config = self.get_config()
        symbol = analysis["symbol"]
        signal = analysis["signal"]

        # Vérifier si on a déjà un trade ouvert sur ce symbole
        existing = [t for t in self.active_trades if t.symbol == symbol and t.status == "OPEN"]
        if existing:
            return {"status": "SKIP", "reason": f"Trade déjà ouvert sur {symbol}"}

        # Vérifier le nombre max de trades
        open_count = len([t for t in self.active_trades if t.status == "OPEN"])
        if open_count >= config["max_open_trades"]:
            return {"status": "SKIP", "reason": f"Max trades ouverts ({config['max_open_trades']})"}

        if signal not in ("STRONG_BUY", "BUY", "STRONG_SELL", "SELL"):
            return {"status": "SKIP", "reason": "Signal HOLD — pas d'action"}

        # Calculer la taille de position
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

            # Vérifier qu'on a assez de cash
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
                order_id=order.order_id or "",
                opened_at=datetime.now().isoformat(),
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

    # ── Monitoring SL/TP ──

    def check_stop_loss_take_profit(self) -> list:
        """Vérifie les SL/TP de tous les trades actifs."""
        if not self.exchange or not self.exchange.connected:
            return []

        closed = []
        for trade in self.active_trades:
            if trade.status != "OPEN":
                continue

            try:
                current_price = self.exchange.get_ticker_price(trade.symbol)
            except Exception:
                continue

            hit = None
            if trade.side == "BUY":
                if current_price <= trade.stop_loss:
                    hit = "SL_HIT"
                elif current_price >= trade.take_profit:
                    hit = "TP_HIT"
                trade.pnl = round((current_price - trade.entry_price) * trade.quantity, 2)
            else:  # SELL (short)
                if current_price >= trade.stop_loss:
                    hit = "SL_HIT"
                elif current_price <= trade.take_profit:
                    hit = "TP_HIT"
                trade.pnl = round((trade.entry_price - current_price) * trade.quantity, 2)

            if hit:
                trade.status = hit
                trade.closed_at = datetime.now().isoformat()

                # Fermer la position
                try:
                    if trade.side == "BUY":
                        self.exchange.sell(trade.symbol, trade.quantity)
                    else:
                        self.exchange.buy(trade.symbol, trade.quantity)
                except Exception:
                    pass

                self.trade_history.append(asdict(trade))
                closed.append(asdict(trade))

        # Nettoyer les trades fermés
        self.active_trades = [t for t in self.active_trades if t.status == "OPEN"]
        self._save_state()
        return closed

    def close_trade(self, symbol: str) -> dict:
        """Ferme manuellement un trade."""
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
                except Exception as e:
                    return {"error": str(e)}

                trade.status = "MANUAL_CLOSE"
                trade.closed_at = datetime.now().isoformat()
                self.trade_history.append(asdict(trade))
                self.active_trades = [t for t in self.active_trades if t.status == "OPEN"]
                self._save_state()
                return {"status": "CLOSED", "trade": asdict(trade)}

        return {"error": f"Aucun trade ouvert sur {symbol}"}

    # ── Boucle automatique ──

    def _trading_loop(self):
        """Boucle principale du trader automatique."""
        while self.running:
            config = self.get_config()
            if not config["enabled"]:
                time.sleep(10)
                continue

            try:
                # 1. Vérifier SL/TP
                self.check_stop_loss_take_profit()

                # 2. Analyser et trader
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
        """Démarre le trader automatique en background."""
        if self.running:
            return {"status": "already_running"}
        self.running = True
        self._thread = threading.Thread(target=self._trading_loop, daemon=True)
        self._thread.start()
        return {"status": "started"}

    def stop(self):
        """Arrête le trader automatique."""
        self.running = False
        return {"status": "stopped"}
