"""
Connecteur Degiro via degiro-connector.

Prérequis:
- Compte Degiro
- pip install degiro-connector
"""

from brokers.base import (
    BaseBroker, BrokerAccount, BrokerPosition, BrokerOrder,
    OrderSide, OrderType,
)
from datetime import datetime


class DegiroBroker(BaseBroker):

    def __init__(self, credentials: dict):
        super().__init__(credentials)
        self.username = credentials.get("username", "")
        self.password = credentials.get("password", "")
        self.totp_secret = credentials.get("totp_secret")
        self.trading_api = None
        self._account_info = None

    def connect(self) -> bool:
        try:
            from degiro_connector.trading.api import API as TradingAPI
            from degiro_connector.trading.models import Credentials

            creds = Credentials(
                username=self.username,
                password=self.password,
                totp_secret_key=self.totp_secret,
            )
            self.trading_api = TradingAPI(credentials=creds)
            self.trading_api.connect()
            self._connected = True
            return True
        except Exception as e:
            print(f"[Degiro] Erreur de connexion: {e}")
            self._connected = False
            return False

    def disconnect(self):
        if self.trading_api:
            try:
                self.trading_api.logout()
            except Exception:
                pass
        self._connected = False

    def get_account(self) -> BrokerAccount:
        self._check_connected()
        account_info = self.trading_api.get_account_info()
        portfolio = self.get_positions()

        cash_funds = account_info.get("cashFunds", {})
        cash = 0
        for fund in cash_funds.get("value", []):
            if fund.get("currencyCode") == "EUR":
                cash = fund.get("value", 0)
                break

        total_value = cash + sum(p.market_value for p in portfolio)

        return BrokerAccount(
            broker_name="Degiro",
            account_id=str(self.trading_api.connection_storage.get("intAccount", "")),
            cash=cash,
            total_value=total_value,
            currency="EUR",
            positions=portfolio,
            connected=True,
        )

    def get_positions(self) -> list[BrokerPosition]:
        self._check_connected()
        from degiro_connector.trading.models import ProductSearch

        portfolio = self.trading_api.get_portfolio()
        positions = []

        for product in portfolio.get("portfolio", {}).get("value", []):
            pos_data = {item["name"]: item["value"] for item in product.get("value", [])}

            if pos_data.get("positionType") != "PRODUCT":
                continue

            product_id = pos_data.get("id")
            shares = float(pos_data.get("size", 0))
            price = float(pos_data.get("price", 0))
            avg_price = float(pos_data.get("breakEvenPrice", price))
            value = float(pos_data.get("value", shares * price))
            pnl = value - (shares * avg_price) if avg_price else 0

            # Récupérer le ticker symbol
            try:
                product_info = self.trading_api.get_product_by_ids(
                    product_list=[int(product_id)]
                )
                ticker = product_info.get(str(product_id), {}).get("symbol", str(product_id))
            except Exception:
                ticker = str(product_id)

            positions.append(BrokerPosition(
                ticker=ticker,
                shares=shares,
                avg_price=avg_price,
                current_price=price,
                market_value=value,
                unrealized_pnl=round(pnl, 2),
                currency=pos_data.get("currency", "EUR"),
            ))

        return positions

    def get_portfolio_value(self) -> float:
        account = self.get_account()
        return account.total_value

    def place_order(self, order: BrokerOrder) -> BrokerOrder:
        self._check_connected()
        from degiro_connector.trading.models import Order

        # Recherche du product_id
        product_id = self._find_product_id(order.ticker)
        if not product_id:
            order.status = "REJECTED"
            return order

        action = Order.Action.BUY if order.side == OrderSide.BUY else Order.Action.SELL

        if order.order_type == OrderType.MARKET:
            degiro_order = Order(
                action=action,
                order_type=Order.OrderType.MARKET,
                product_id=product_id,
                size=order.quantity,
                time_type=Order.TimeType.DAY,
            )
        else:
            degiro_order = Order(
                action=action,
                order_type=Order.OrderType.LIMIT,
                product_id=product_id,
                size=order.quantity,
                price=order.limit_price,
                time_type=Order.TimeType.DAY,
            )

        # Vérification puis confirmation
        check = self.trading_api.check_order(order=degiro_order)
        if check:
            confirmation = self.trading_api.confirm_order(
                confirmation_id=check.get("confirmationId"),
                order=degiro_order,
            )
            order.order_id = str(confirmation) if confirmation else None
            order.status = "SUBMITTED" if confirmation else "REJECTED"
        else:
            order.status = "REJECTED"

        return order

    def get_order_status(self, order_id: str) -> BrokerOrder:
        self._check_connected()
        orders = self.trading_api.get_orders()
        for o in orders.get("orders", {}).get("value", []):
            o_data = {item["name"]: item["value"] for item in o.get("value", [])}
            if str(o_data.get("orderId")) == order_id:
                return BrokerOrder(
                    ticker=o_data.get("productId", ""),
                    side=OrderSide.BUY if o_data.get("buysell") == "B" else OrderSide.SELL,
                    quantity=int(o_data.get("size", 0)),
                    order_id=order_id,
                    status=o_data.get("status", "UNKNOWN"),
                    filled_price=float(o_data.get("price", 0)),
                )
        return BrokerOrder(ticker="", side=OrderSide.BUY, quantity=0,
                          order_id=order_id, status="NOT_FOUND")

    def cancel_order(self, order_id: str) -> bool:
        self._check_connected()
        try:
            return self.trading_api.delete_order(order_id=order_id)
        except Exception:
            return False

    def _find_product_id(self, ticker: str) -> int:
        """Cherche le product_id Degiro à partir du ticker."""
        try:
            results = self.trading_api.product_search(
                text=ticker.split(".")[0],
                limit=5,
            )
            for product in results.get("products", []):
                if product.get("symbol", "").upper() == ticker.split(".")[0].upper():
                    return int(product["id"])
        except Exception:
            pass
        return None

    def _check_connected(self):
        if not self._connected or not self.trading_api:
            raise ConnectionError("Non connecté à Degiro.")
