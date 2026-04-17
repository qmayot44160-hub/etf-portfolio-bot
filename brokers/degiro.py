"""
Connecteur Degiro via degiro-connector v3.

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
        self.totp_secret = credentials.get("totp_secret") or None
        self.one_time_password = credentials.get("one_time_password") or None
        self.trading_api = None

    def connect(self) -> bool:
        from degiro_connector.trading.api import API as TradingAPI
        from degiro_connector.trading.models.credentials import Credentials

        otp = int(self.one_time_password) if self.one_time_password else None

        creds = Credentials(
            username=self.username,
            password=self.password,
            totp_secret_key=self.totp_secret,
            one_time_password=otp,
        )
        self.trading_api = TradingAPI(credentials=creds)
        self.trading_api.connect()
        self._connected = True
        return True

    def disconnect(self):
        if self.trading_api:
            try:
                self.trading_api.logout()
            except Exception:
                pass
        self._connected = False

    def _get_update(self, options: list) -> dict:
        """Récupère les données du compte via get_update."""
        from degiro_connector.trading.models.account import UpdateRequest, UpdateOption

        request_list = [UpdateRequest(option=opt, last_updated=0) for opt in options]
        update = self.trading_api.get_update(request_list=request_list, raw=True)
        return update if isinstance(update, dict) else {}

    def get_account(self) -> BrokerAccount:
        self._check_connected()
        from degiro_connector.trading.models.account import UpdateOption

        data = self._get_update([
            UpdateOption.PORTFOLIO,
            UpdateOption.CASH_FUNDS,
            UpdateOption.TOTAL_PORTFOLIO,
        ])

        positions = self._parse_positions(data.get("portfolio", {}))

        # Cash
        cash = 0
        cash_funds = data.get("cashFunds", data.get("cash_funds", {}))
        if isinstance(cash_funds, dict):
            for item in cash_funds.get("value", []):
                values = {v["name"]: v["value"] for v in item.get("value", [])} if "value" in item else item
                if values.get("currencyCode") == "EUR":
                    cash = float(values.get("value", 0))
                    break

        # Total
        total_portfolio = data.get("totalPortfolio", data.get("total_portfolio", {}))
        total_value = cash
        if isinstance(total_portfolio, dict):
            values = total_portfolio.get("value", total_portfolio)
            if isinstance(values, list):
                vals = {v["name"]: v["value"] for v in values}
                total_value = float(vals.get("degiroCash", cash)) + sum(p.market_value for p in positions)
            elif isinstance(values, dict):
                total_value = float(values.get("degiroCash", cash)) + sum(p.market_value for p in positions)
        else:
            total_value = cash + sum(p.market_value for p in positions)

        return BrokerAccount(
            broker_name="Degiro",
            account_id="",
            cash=round(cash, 2),
            total_value=round(total_value, 2),
            currency="EUR",
            positions=positions,
            connected=True,
        )

    def _parse_positions(self, portfolio_data: dict) -> list[BrokerPosition]:
        """Parse les positions depuis les données portfolio."""
        positions = []
        items = portfolio_data.get("value", [])
        product_ids = []

        parsed = []
        for item in items:
            if isinstance(item, dict) and "value" in item:
                pos_data = {v["name"]: v["value"] for v in item["value"]}
            else:
                pos_data = item if isinstance(item, dict) else {}

            if pos_data.get("positionType") != "PRODUCT":
                continue

            product_id = pos_data.get("id")
            shares = float(pos_data.get("size", 0))
            price = float(pos_data.get("price", 0))
            avg_price = float(pos_data.get("breakEvenPrice", price))
            value = float(pos_data.get("value", shares * price))
            pnl = value - (shares * avg_price) if avg_price else 0

            if product_id:
                product_ids.append(int(product_id))
            parsed.append({
                "product_id": product_id,
                "shares": shares,
                "price": price,
                "avg_price": avg_price,
                "value": value,
                "pnl": pnl,
                "currency": pos_data.get("currency", "EUR"),
            })

        # Récupérer les symboles
        symbols = {}
        if product_ids:
            try:
                products_info = self.trading_api.get_products_info(
                    product_list=product_ids,
                    raw=True,
                )
                if isinstance(products_info, dict):
                    for pid, info in products_info.get("data", products_info).items():
                        symbols[str(pid)] = info.get("symbol", str(pid))
            except Exception:
                pass

        for p in parsed:
            pid = str(p["product_id"])
            ticker = symbols.get(pid, pid)
            positions.append(BrokerPosition(
                ticker=ticker,
                shares=p["shares"],
                avg_price=p["avg_price"],
                current_price=p["price"],
                market_value=round(p["value"], 2),
                unrealized_pnl=round(p["pnl"], 2),
                currency=p["currency"],
            ))

        return positions

    def get_positions(self) -> list[BrokerPosition]:
        self._check_connected()
        from degiro_connector.trading.models.account import UpdateOption
        data = self._get_update([UpdateOption.PORTFOLIO])
        return self._parse_positions(data.get("portfolio", {}))

    def get_portfolio_value(self) -> float:
        account = self.get_account()
        return account.total_value

    def _place_order_impl(self, order: BrokerOrder) -> BrokerOrder:
        self._check_connected()
        from degiro_connector.trading.models.order import (
            Order as DegiroOrder, Action, OrderType as DOrderType, TimeType,
        )

        product_id = self._find_product_id(order.ticker)
        if not product_id:
            order.status = "REJECTED - Produit non trouvé"
            return order

        action = Action.BUY if order.side == OrderSide.BUY else Action.SELL

        if order.order_type == OrderType.MARKET:
            d_order = DegiroOrder(
                buy_sell=action,
                order_type=DOrderType.MARKET,
                product_id=product_id,
                size=order.quantity,
                time_type=TimeType.GOOD_TILL_DAY,
            )
        else:
            d_order = DegiroOrder(
                buy_sell=action,
                order_type=DOrderType.LIMIT,
                product_id=product_id,
                size=order.quantity,
                price=order.limit_price,
                time_type=TimeType.GOOD_TILL_DAY,
            )

        try:
            check = self.trading_api.check_order(order=d_order)
            if check:
                conf_id = check.get("confirmationId") if isinstance(check, dict) else getattr(check, "confirmation_id", None)
                if conf_id:
                    confirmation = self.trading_api.confirm_order(
                        confirmation_id=conf_id,
                        order=d_order,
                    )
                    order.order_id = str(confirmation) if confirmation else None
                    order.status = "SUBMITTED" if confirmation else "REJECTED"
                else:
                    order.status = "REJECTED - Pas de confirmation ID"
            else:
                order.status = "REJECTED - Check échoué"
        except Exception as e:
            order.status = f"REJECTED - {str(e)}"

        return order

    def get_order_status(self, order_id: str) -> BrokerOrder:
        self._check_connected()
        from degiro_connector.trading.models.account import UpdateOption
        data = self._get_update([UpdateOption.ORDERS])

        orders = data.get("orders", {}).get("value", [])
        for o in orders:
            o_data = {v["name"]: v["value"] for v in o.get("value", [])} if "value" in o else o
            if str(o_data.get("orderId", o_data.get("id", ""))) == order_id:
                side = OrderSide.BUY if o_data.get("buysell", o_data.get("buy_sell")) == "BUY" else OrderSide.SELL
                return BrokerOrder(
                    ticker=str(o_data.get("productId", o_data.get("product_id", ""))),
                    side=side,
                    quantity=int(o_data.get("size", 0)),
                    order_id=order_id,
                    status=str(o_data.get("status", "UNKNOWN")),
                    filled_price=float(o_data.get("price", 0)),
                )
        return BrokerOrder(ticker="", side=OrderSide.BUY, quantity=0,
                          order_id=order_id, status="NOT_FOUND")

    def cancel_order(self, order_id: str) -> bool:
        self._check_connected()
        try:
            return bool(self.trading_api.delete_order(order_id=order_id))
        except Exception:
            return False

    def _find_product_id(self, ticker: str) -> int:
        """Cherche le product_id Degiro à partir du ticker."""
        try:
            symbol = ticker.split(".")[0]
            results = self.trading_api.product_search(text=symbol, limit=10, raw=True)
            products = results if isinstance(results, list) else results.get("products", [])
            for product in products:
                if isinstance(product, dict):
                    if product.get("symbol", "").upper() == symbol.upper():
                        return int(product["id"])
            # Fallback: premier résultat
            if products:
                first = products[0]
                return int(first["id"]) if isinstance(first, dict) else None
        except Exception:
            pass
        return None

    def _check_connected(self):
        if not self._connected or not self.trading_api:
            raise ConnectionError("Non connecté à Degiro. Allez dans l'onglet Broker pour vous connecter.")
