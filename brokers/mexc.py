"""
Connecteur MEXC via ccxt — exchange crypto.

Prérequis:
- Compte MEXC avec clés API
- pip install ccxt
"""

from brokers.base import (
    BaseBroker, BrokerAccount, BrokerPosition, BrokerOrder,
    OrderSide, OrderType,
)
from datetime import datetime


class MEXCBroker(BaseBroker):

    def __init__(self, credentials: dict):
        super().__init__(credentials)
        self.api_key = credentials.get("api_key", "")
        self.secret_key = credentials.get("secret_key", "")
        self.exchange = None

    def connect(self) -> bool:
        import ccxt
        self.exchange = ccxt.mexc({
            "apiKey": self.api_key,
            "secret": self.secret_key,
            "options": {"defaultType": "spot"},
        })
        # Test connexion en récupérant le solde
        balance = self.exchange.fetch_balance()
        if balance:
            self._connected = True
            return True
        raise ConnectionError("Impossible de récupérer le solde MEXC")

    def disconnect(self):
        self.exchange = None
        self._connected = False

    def get_account(self) -> BrokerAccount:
        self._check_connected()
        balance = self.exchange.fetch_balance()
        positions = self._parse_balance(balance)
        total_usdt = self._total_in_usdt(positions, balance)

        return BrokerAccount(
            broker_name="MEXC",
            account_id="spot",
            cash=round(float(balance.get("USDT", {}).get("free", 0)), 2),
            total_value=round(total_usdt, 2),
            currency="USDT",
            positions=positions,
            connected=True,
        )

    def _parse_balance(self, balance: dict) -> list[BrokerPosition]:
        """Parse les soldes non nuls en positions."""
        positions = []
        tickers_to_fetch = []

        for currency, amounts in balance.items():
            if currency in ("info", "free", "used", "total", "timestamp", "datetime"):
                continue
            if not isinstance(amounts, dict):
                continue
            total = float(amounts.get("total", 0))
            if total <= 0:
                continue
            tickers_to_fetch.append(currency)

        # Récupérer les prix actuels
        prices = {}
        for curr in tickers_to_fetch:
            if curr == "USDT":
                prices[curr] = 1.0
                continue
            try:
                ticker = self.exchange.fetch_ticker(f"{curr}/USDT")
                prices[curr] = float(ticker["last"]) if ticker.get("last") else 0
            except Exception:
                prices[curr] = 0

        for currency in tickers_to_fetch:
            amounts = balance[currency]
            total = float(amounts.get("total", 0))
            price = prices.get(currency, 0)
            value = total * price

            positions.append(BrokerPosition(
                ticker=currency,
                shares=total,
                avg_price=0,  # MEXC ne fournit pas le prix moyen d'achat
                current_price=price,
                market_value=round(value, 2),
                unrealized_pnl=0,
                currency="USDT",
            ))

        return positions

    def _total_in_usdt(self, positions: list, balance: dict) -> float:
        """Calcule la valeur totale en USDT."""
        return sum(p.market_value for p in positions)

    def get_positions(self) -> list[BrokerPosition]:
        self._check_connected()
        balance = self.exchange.fetch_balance()
        return self._parse_balance(balance)

    def get_portfolio_value(self) -> float:
        account = self.get_account()
        return account.total_value

    def place_order(self, order: BrokerOrder) -> BrokerOrder:
        self._check_connected()
        symbol = order.ticker if "/" in order.ticker else f"{order.ticker}/USDT"
        side = "buy" if order.side == OrderSide.BUY else "sell"

        try:
            if order.order_type == OrderType.MARKET:
                result = self.exchange.create_market_order(
                    symbol=symbol,
                    side=side,
                    amount=order.quantity,
                )
            else:
                result = self.exchange.create_limit_order(
                    symbol=symbol,
                    side=side,
                    amount=order.quantity,
                    price=order.limit_price,
                )

            order.order_id = str(result.get("id", ""))
            order.status = result.get("status", "SUBMITTED")
            order.filled_price = float(result.get("average") or result.get("price") or 0)
            if result.get("datetime"):
                order.filled_at = result["datetime"]
            return order
        except Exception as e:
            order.status = f"REJECTED - {str(e)}"
            return order

    def get_order_status(self, order_id: str) -> BrokerOrder:
        self._check_connected()
        try:
            # On doit chercher dans les ordres ouverts et fermés
            open_orders = self.exchange.fetch_open_orders()
            for o in open_orders:
                if str(o["id"]) == order_id:
                    return self._parse_order(o)

            closed_orders = self.exchange.fetch_closed_orders()
            for o in closed_orders:
                if str(o["id"]) == order_id:
                    return self._parse_order(o)
        except Exception:
            pass
        return BrokerOrder(ticker="", side=OrderSide.BUY, quantity=0,
                          order_id=order_id, status="NOT_FOUND")

    def cancel_order(self, order_id: str) -> bool:
        self._check_connected()
        try:
            self.exchange.cancel_order(order_id)
            return True
        except Exception:
            return False

    def _parse_order(self, o: dict) -> BrokerOrder:
        side = OrderSide.BUY if o.get("side") == "buy" else OrderSide.SELL
        return BrokerOrder(
            ticker=o.get("symbol", ""),
            side=side,
            quantity=int(float(o.get("amount", 0))),
            order_id=str(o["id"]),
            status=o.get("status", "UNKNOWN"),
            filled_price=float(o.get("average", 0) or 0),
            filled_at=o.get("datetime"),
        )

    # ── Méthodes spécifiques crypto ──

    def get_all_prices(self, symbols: list[str] = None) -> dict:
        """Récupère les prix de plusieurs cryptos."""
        self._check_connected()
        if symbols:
            pairs = [f"{s}/USDT" for s in symbols]
            tickers = self.exchange.fetch_tickers(pairs)
        else:
            tickers = self.exchange.fetch_tickers()
        return {
            symbol.replace("/USDT", ""): float(data["last"])
            for symbol, data in tickers.items()
            if data.get("last") and "/USDT" in symbol
        }

    def get_ticker_price(self, symbol: str) -> float:
        """Prix d'une crypto spécifique."""
        self._check_connected()
        pair = symbol if "/" in symbol else f"{symbol}/USDT"
        ticker = self.exchange.fetch_ticker(pair)
        return float(ticker["last"]) if ticker.get("last") else 0

    def _check_connected(self):
        if not self._connected or not self.exchange:
            raise ConnectionError("Non connecté à MEXC. Allez dans l'onglet Broker pour vous connecter.")
