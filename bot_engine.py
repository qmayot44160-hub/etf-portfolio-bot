"""
Moteur principal du bot — orchestre broker, portefeuille et stratégie.
"""

import json
import os
from datetime import datetime
from dataclasses import asdict
from config import PORTFOLIO, DCA_MONTHLY, REBALANCE_THRESHOLD_PCT
from brokers import get_broker, list_brokers, BrokerOrder, OrderSide, OrderType
from brokers.base import BaseBroker
from market_data import get_current_prices
from portfolio_allocation import allocate_by_target, compute_rebalance_orders
from data_paths import data_path

BROKER_CONFIG_FILE = data_path("broker_config.json")


class BotEngine:
    def __init__(self):
        self.broker: BaseBroker = None
        self._load_broker_config()

    def _load_broker_config(self):
        """Charge et connecte le broker sauvegardé."""
        if os.path.exists(BROKER_CONFIG_FILE):
            with open(BROKER_CONFIG_FILE, "r") as f:
                config = json.load(f)
            broker_id = config.get("broker_id")
            credentials = config.get("credentials", {})
            if broker_id:
                try:
                    self.broker = get_broker(broker_id, credentials)
                except Exception:
                    pass

    def save_broker_config(self, broker_id: str, credentials: dict):
        """Sauvegarde la config du broker."""
        config = {
            "broker_id": broker_id,
            "credentials": credentials,
            "configured_at": datetime.now().isoformat(),
        }
        with open(BROKER_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)

    def connect_broker(self, broker_id: str, credentials: dict) -> dict:
        """Connecte un broker et sauvegarde la config."""
        try:
            self.broker = get_broker(broker_id, credentials)
            success = self.broker.connect()
            if success:
                self.save_broker_config(broker_id, credentials)
                return {"status": "connected", "broker": self.broker.name}
            return {"status": "error", "message": "Connexion échouée — vérifiez vos identifiants"}
        except ImportError as e:
            return {"status": "error", "message": str(e)}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def disconnect_broker(self):
        """Déconnecte le broker actuel."""
        if self.broker:
            self.broker.disconnect()
            self.broker = None
        if os.path.exists(BROKER_CONFIG_FILE):
            os.remove(BROKER_CONFIG_FILE)
        return {"status": "disconnected"}

    def get_status(self) -> dict:
        """Retourne le statut complet du bot."""
        status = {
            "broker_connected": False,
            "broker_name": None,
            "mode": "simulation",
        }

        if self.broker and self.broker.connected:
            status["broker_connected"] = True
            status["broker_name"] = self.broker.name
            status["mode"] = "live"

        return status

    def get_portfolio(self) -> dict:
        """
        Récupère le portefeuille — du broker si connecté, sinon message non connecté.
        """
        if self.broker and self.broker.connected:
            return self._get_broker_portfolio()
        return {
            "mode": "not_connected",
            "broker": None,
            "total_value": 0,
            "cash": 0,
            "currency": "EUR",
            "positions": [],
            "last_update": None,
            "message": "Aucun broker ETF connecté. Connectez IBKR ou un autre broker pour voir votre portefeuille réel.",
        }

    def _get_broker_portfolio(self) -> dict:
        """Portefeuille réel depuis le broker."""
        try:
            account = self.broker.get_account()
            positions = []
            for pos in account.positions:
                # Trouver la config cible pour ce ticker
                target = self._find_target(pos.ticker)
                target_pct = target["target_pct"] if target else 0

                current_pct = (pos.market_value / account.total_value * 100
                               if account.total_value > 0 else 0)

                positions.append({
                    "ticker": pos.ticker,
                    "name": target["name"] if target else pos.ticker,
                    "category": target["category"] if target else "Autre",
                    "shares": pos.shares,
                    "avg_price": pos.avg_price,
                    "price": pos.current_price,
                    "value": round(pos.market_value, 2),
                    "target_pct": target_pct,
                    "current_pct": round(current_pct, 2),
                    "drift": round(current_pct - target_pct, 2),
                    "unrealized_pnl": round(pos.unrealized_pnl, 2),
                })

            return {
                "mode": "live",
                "broker": account.broker_name,
                "total_value": round(account.total_value, 2),
                "cash": round(account.cash, 2),
                "currency": account.currency,
                "positions": positions,
                "last_update": datetime.now().isoformat(),
            }
        except Exception as e:
            return {"error": str(e), "mode": "live"}

    def _get_simulated_portfolio(self) -> dict:
        """Portefeuille simulé (mode sans broker)."""
        from portfolio import get_portfolio_value
        result = get_portfolio_value()
        result["mode"] = "simulation"
        result["broker"] = None
        return result

    def execute_dca(self, amount: float = None) -> list:
        """Exécute un DCA — via broker ou simulation."""
        if amount is None:
            amount = DCA_MONTHLY

        if self.broker and self.broker.connected:
            return self._broker_dca(amount)

        from portfolio import execute_dca as sim_dca
        return sim_dca(amount=amount)

    def _broker_dca(self, amount: float) -> list:
        """DCA via le broker réel — calcul d'allocation partagé, puis ordres."""
        allocation = allocate_by_target(amount, PORTFOLIO, get_current_prices())
        results = []

        for alloc in allocation:
            shares = alloc["shares_to_buy"]
            if shares <= 0:
                continue
            ticker = alloc["ticker"]
            try:
                order = self.broker.buy(ticker, shares)
                results.append({
                    "ticker": ticker,
                    "name": alloc["name"],
                    "shares": shares,
                    "price": alloc["price"],
                    "amount": alloc["real_amount"],
                    "order_id": order.order_id,
                    "status": order.status,
                })
            except Exception as e:
                results.append({
                    "ticker": ticker,
                    "name": alloc["name"],
                    "shares": shares,
                    "error": str(e),
                    "status": "FAILED",
                })

        return results

    def execute_rebalance(self) -> list:
        """Calcule (via helper partagé) puis exécute le rééquilibrage."""
        portfolio = self.get_portfolio()
        if "error" in portfolio:
            return [{"error": portfolio["error"]}]

        orders = compute_rebalance_orders(
            portfolio["positions"], portfolio["total_value"], REBALANCE_THRESHOLD_PCT
        )
        results = []

        for o in orders:
            ticker = o["ticker"]
            shares = o["shares"]
            action = o["action"]
            try:
                if self.broker and self.broker.connected:
                    order = (self.broker.buy(ticker, shares)
                             if action == "BUY"
                             else self.broker.sell(ticker, shares))
                    results.append({
                        "ticker": ticker,
                        "name": o["name"],
                        "action": action,
                        "shares": shares,
                        "amount": o["amount"],
                        "order_id": order.order_id,
                        "status": order.status,
                    })
                else:
                    results.append({
                        "ticker": ticker,
                        "name": o["name"],
                        "action": action,
                        "shares": shares,
                        "amount": o["amount"],
                        "status": "SIMULATED",
                    })
            except Exception as e:
                results.append({
                    "ticker": ticker,
                    "error": str(e),
                    "status": "FAILED",
                })

        return results

    def _find_target(self, ticker: str) -> dict:
        """Cherche la config cible d'un ticker (matching flexible)."""
        # Correspondance exacte
        if ticker in PORTFOLIO:
            return PORTFOLIO[ticker]
        # Correspondance par symbole (sans suffixe de bourse)
        symbol = ticker.split(".")[0].upper()
        for t, config in PORTFOLIO.items():
            if t.split(".")[0].upper() == symbol:
                return config
        return None
