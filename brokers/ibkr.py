"""
Connecteur Interactive Brokers (IBKR) via ib_insync.

Prérequis:
- Compte IBKR (même Paper Trading)
- TWS ou IB Gateway en cours d'exécution
- pip install ib_insync
"""

from brokers.base import (
    BaseBroker, BrokerAccount, BrokerPosition, BrokerOrder,
    OrderSide, OrderType,
)
from datetime import datetime


class IBKRBroker(BaseBroker):

    def __init__(self, credentials: dict):
        super().__init__(credentials)
        self.host = credentials.get("host", "127.0.0.1")
        self.port = credentials.get("port", 7497)  # 7497=paper, 7496=live
        self.client_id = credentials.get("client_id", 1)
        self.ib = None

    def connect(self) -> bool:
        try:
            from ib_insync import IB
            self.ib = IB()
            self.ib.connect(self.host, self.port, clientId=self.client_id)
            self._connected = self.ib.isConnected()
            return self._connected
        except Exception as e:
            print(f"[IBKR] Erreur de connexion: {e}")
            self._connected = False
            return False

    def disconnect(self):
        if self.ib:
            self.ib.disconnect()
        self._connected = False

    def get_account(self) -> BrokerAccount:
        self._check_connected()
        summary = {s.tag: s.value for s in self.ib.accountSummary()
                   if s.currency in ("EUR", "USD", "BASE")}
        positions = self.get_positions()
        total = float(summary.get("NetLiquidation", 0))
        cash = float(summary.get("TotalCashValue", 0))
        currency = summary.get("Currency", "EUR")

        return BrokerAccount(
            broker_name="Interactive Brokers",
            account_id=self.ib.managedAccounts()[0] if self.ib.managedAccounts() else "",
            cash=cash,
            total_value=total,
            currency=currency,
            positions=positions,
            connected=True,
        )

    def get_positions(self) -> list[BrokerPosition]:
        self._check_connected()
        positions = []
        for pos in self.ib.positions():
            contract = pos.contract
            ticker = f"{contract.symbol}.{contract.exchange}" if contract.exchange else contract.symbol
            avg_price = pos.avgCost
            shares = pos.position

            # Prix actuel via ticker
            market_data = self.ib.reqMktData(contract, snapshot=True)
            self.ib.sleep(1)
            current_price = market_data.marketPrice() or avg_price
            market_value = shares * current_price
            pnl = market_value - (shares * avg_price)

            positions.append(BrokerPosition(
                ticker=ticker,
                shares=float(shares),
                avg_price=float(avg_price),
                current_price=float(current_price),
                market_value=float(market_value),
                unrealized_pnl=float(pnl),
                currency=contract.currency,
            ))
        return positions

    def get_portfolio_value(self) -> float:
        account = self.get_account()
        return account.total_value

    def place_order(self, order: BrokerOrder) -> BrokerOrder:
        self._check_connected()
        from ib_insync import Stock, MarketOrder, LimitOrder

        contract = Stock(order.ticker.split(".")[0], "SMART", "EUR")
        self.ib.qualifyContracts(contract)

        if order.order_type == OrderType.MARKET:
            ib_order = MarketOrder(order.side.value, order.quantity)
        else:
            ib_order = LimitOrder(order.side.value, order.quantity, order.limit_price)

        trade = self.ib.placeOrder(contract, ib_order)
        self.ib.sleep(1)

        order.order_id = str(trade.order.orderId)
        order.status = trade.orderStatus.status
        if trade.fills:
            order.filled_price = trade.fills[0].execution.price
            order.filled_at = datetime.now().isoformat()

        return order

    def get_order_status(self, order_id: str) -> BrokerOrder:
        self._check_connected()
        for trade in self.ib.trades():
            if str(trade.order.orderId) == order_id:
                return BrokerOrder(
                    ticker=trade.contract.symbol,
                    side=OrderSide(trade.order.action),
                    quantity=int(trade.order.totalQuantity),
                    order_id=order_id,
                    status=trade.orderStatus.status,
                    filled_price=trade.orderStatus.avgFillPrice or None,
                )
        return BrokerOrder(ticker="", side=OrderSide.BUY, quantity=0,
                          order_id=order_id, status="NOT_FOUND")

    def cancel_order(self, order_id: str) -> bool:
        self._check_connected()
        for trade in self.ib.trades():
            if str(trade.order.orderId) == order_id:
                self.ib.cancelOrder(trade.order)
                return True
        return False

    def _check_connected(self):
        if not self._connected or not self.ib or not self.ib.isConnected():
            raise ConnectionError("Non connecté à IBKR. Lancez TWS/IB Gateway d'abord.")
