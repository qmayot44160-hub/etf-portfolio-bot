"""
Moteur crypto — gestion du portefeuille crypto via MEXC.
"""

import json
import os
from datetime import datetime
from config import CRYPTO_PORTFOLIO, CRYPTO_DCA_MONTHLY, REBALANCE_THRESHOLD_PCT
from brokers import get_broker
from brokers.base import BaseBroker


class CryptoEngine:
    def __init__(self):
        self.broker: BaseBroker = None
        self._load_config()

    def _load_config(self):
        if os.path.exists("crypto_config.json"):
            with open("crypto_config.json", "r") as f:
                config = json.load(f)
            broker_id = config.get("broker_id")
            credentials = config.get("credentials", {})
            if broker_id:
                try:
                    self.broker = get_broker(broker_id, credentials)
                    self.broker.connect()
                except Exception as e:
                    print(f"[CryptoEngine] Auto-reconnect failed: {e}")
                    self.broker = None

    def connect(self, broker_id: str, credentials: dict) -> dict:
        try:
            self.broker = get_broker(broker_id, credentials)
            success = self.broker.connect()
            if success:
                with open("crypto_config.json", "w") as f:
                    json.dump({
                        "broker_id": broker_id,
                        "credentials": credentials,
                        "connected_at": datetime.now().isoformat(),
                    }, f, indent=2)
                return {"status": "connected", "broker": self.broker.name}
            return {"status": "error", "message": "Connexion échouée"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def disconnect(self):
        if self.broker:
            self.broker.disconnect()
            self.broker = None
        if os.path.exists("crypto_config.json"):
            os.remove("crypto_config.json")
        return {"status": "disconnected"}

    def get_status(self) -> dict:
        return {
            "connected": bool(self.broker and self.broker.connected),
            "broker_name": self.broker.name if self.broker else None,
        }

    def get_portfolio(self) -> dict:
        if not self.broker or not self.broker.connected:
            return {"error": "Aucun exchange crypto connecté", "positions": []}

        try:
            account = self.broker.get_account()
            positions = []
            tracked_symbols = set(CRYPTO_PORTFOLIO.keys())

            for pos in account.positions:
                symbol = pos.ticker.upper()
                config = CRYPTO_PORTFOLIO.get(symbol)
                target_pct = config["target_pct"] if config else 0
                current_pct = (pos.market_value / account.total_value * 100
                               if account.total_value > 0 else 0)

                positions.append({
                    "ticker": symbol,
                    "name": config["name"] if config else symbol,
                    "category": config["category"] if config else "Autre",
                    "amount": pos.shares,
                    "price": pos.current_price,
                    "value": round(pos.market_value, 2),
                    "target_pct": target_pct,
                    "current_pct": round(current_pct, 2),
                    "drift": round(current_pct - target_pct, 2),
                    "tracked": symbol in tracked_symbols,
                })

            # Ajouter les cryptos cibles non détenues
            held = {p["ticker"] for p in positions}
            for symbol, config in CRYPTO_PORTFOLIO.items():
                if symbol not in held:
                    positions.append({
                        "ticker": symbol,
                        "name": config["name"],
                        "category": config["category"],
                        "amount": 0,
                        "price": 0,
                        "value": 0,
                        "target_pct": config["target_pct"],
                        "current_pct": 0,
                        "drift": -config["target_pct"],
                        "tracked": True,
                    })

            # Trier : tracked d'abord, puis par valeur décroissante
            positions.sort(key=lambda p: (-p["tracked"], -p["value"]))

            return {
                "total_value": round(account.total_value, 2),
                "cash": round(account.cash, 2),
                "currency": "USDT",
                "positions": positions,
                "last_update": datetime.now().isoformat(),
            }
        except Exception as e:
            return {"error": str(e), "positions": []}

    def calculate_dca(self, amount: float = None) -> list:
        if amount is None:
            amount = CRYPTO_DCA_MONTHLY

        if not self.broker or not self.broker.connected:
            return [{"error": "Non connecté"}]

        allocation = []
        for symbol, config in CRYPTO_PORTFOLIO.items():
            alloc_amount = amount * config["target_pct"] / 100
            try:
                price = self.broker.get_ticker_price(symbol)
            except Exception:
                price = 0

            qty = round(alloc_amount / price, 6) if price > 0 else 0
            allocation.append({
                "ticker": symbol,
                "name": config["name"],
                "target_amount": round(alloc_amount, 2),
                "price": round(price, 4),
                "quantity": qty,
                "real_amount": round(qty * price, 2),
            })
        return allocation

    def execute_dca(self, amount: float = None) -> list:
        if amount is None:
            amount = CRYPTO_DCA_MONTHLY

        if not self.broker or not self.broker.connected:
            return [{"error": "Non connecté"}]

        allocation = self.calculate_dca(amount)
        results = []

        for alloc in allocation:
            if alloc.get("error") or alloc["quantity"] <= 0:
                continue
            try:
                order = self.broker.buy(alloc["ticker"], alloc["quantity"])
                results.append({
                    **alloc,
                    "order_id": order.order_id,
                    "status": order.status,
                })
            except Exception as e:
                results.append({
                    **alloc,
                    "status": f"FAILED - {str(e)}",
                })

        return results

    def calculate_rebalance(self) -> list:
        portfolio = self.get_portfolio()
        if "error" in portfolio and not portfolio.get("positions"):
            return []

        total = portfolio["total_value"]
        orders = []

        for pos in portfolio["positions"]:
            if not pos["tracked"] or abs(pos["drift"]) < REBALANCE_THRESHOLD_PCT:
                continue

            target_value = total * pos["target_pct"] / 100
            diff = target_value - pos["value"]
            price = pos["price"]
            if not price or price <= 0:
                continue

            qty = round(abs(diff) / price, 6)
            if qty <= 0:
                continue

            orders.append({
                "ticker": pos["ticker"],
                "name": pos["name"],
                "action": "BUY" if diff > 0 else "SELL",
                "quantity": qty,
                "price": price,
                "amount": round(abs(diff), 2),
                "drift": pos["drift"],
            })

        return orders
