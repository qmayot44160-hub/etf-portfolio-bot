"""
Connecteur Alpaca — broker gratuit avec API moderne.

Prérequis:
- Compte Alpaca (paper ou live)
- pip install alpaca-trade-api
"""

from brokers.base import (
    BaseBroker, BrokerAccount, BrokerPosition, BrokerOrder,
    OrderSide, OrderType,
)
from datetime import datetime


class AlpacaBroker(BaseBroker):

    def __init__(self, credentials: dict):
        super().__init__(credentials)
        self.api_key = credentials.get("api_key", "")
        self.secret_key = credentials.get("secret_key", "")
        self.paper = credentials.get("paper", True)
        self.api = None

    def connect(self) -> bool:
        try:
            from alpaca_trade_api import REST
            base_url = (
                "https://paper-api.alpaca.markets" if self.paper
                else "https://api.alpaca.markets"
            )
            self.api = REST(self.api_key, self.secret_key, base_url)
            # Test de connexion
            self.api.get_account()
            self._connected = True
            return True
        except Exception as e:
            print(f"[Alpaca] Erreur de connexion: {e}")
            self._connected = False
            return False

    def disconnect(self):
        self.api = None
        self._connected = False

    def get_account(self) -> BrokerAccount:
        self._check_connected()
        account = self.api.get_account()
        positions = self.get_positions()

        return BrokerAccount(
            broker_name="Alpaca",
            account_id=account.id,
            cash=float(account.cash),
            total_value=float(account.portfolio_value),
            currency="USD",
            positions=positions,
            connected=True,
        )

    def get_positions(self) -> list[BrokerPosition]:
        self._check_connected()
        positions = []
        for pos in self.api.list_positions():
            positions.append(BrokerPosition(
                ticker=pos.symbol,
                shares=float(pos.qty),
                avg_price=float(pos.avg_entry_price),
                current_price=float(pos.current_price),
                market_value=float(pos.market_value),
                unrealized_pnl=float(pos.unrealized_pl),
                currency="USD",
            ))
        return positions

    def get_portfolio_value(self) -> float:
        self._check_connected()
        account = self.api.get_account()
        return float(account.portfolio_value)

    def _place_order_impl(self, order: BrokerOrder) -> BrokerOrder:
        self._check_connected()
        try:
            side = "buy" if order.side == OrderSide.BUY else "sell"
            order_type = "market" if order.order_type == OrderType.MARKET else "limit"

            kwargs = {
                "symbol": order.ticker,
                "qty": order.quantity,
                "side": side,
                "type": order_type,
                "time_in_force": "day",
            }
            if order_type == "limit":
                kwargs["limit_price"] = order.limit_price

            result = self.api.submit_order(**kwargs)
            order.order_id = result.id
            order.status = result.status
            if result.filled_avg_price:
                order.filled_price = float(result.filled_avg_price)
                order.filled_at = result.filled_at
            return order
        except Exception as e:
            order.status = f"REJECTED: {e}"
            return order

    def get_order_status(self, order_id: str) -> BrokerOrder:
        self._check_connected()
        try:
            result = self.api.get_order(order_id)
            side = OrderSide.BUY if result.side == "buy" else OrderSide.SELL
            return BrokerOrder(
                ticker=result.symbol,
                side=side,
                quantity=int(float(result.qty)),
                order_id=order_id,
                status=result.status,
                filled_price=float(result.filled_avg_price) if result.filled_avg_price else None,
            )
        except Exception:
            return BrokerOrder(ticker="", side=OrderSide.BUY, quantity=0,
                              order_id=order_id, status="NOT_FOUND")

    def cancel_order(self, order_id: str) -> bool:
        self._check_connected()
        try:
            self.api.cancel_order(order_id)
            return True
        except Exception:
            return False

    def _check_connected(self):
        if not self._connected or not self.api:
            raise ConnectionError("Non connecté à Alpaca.")
