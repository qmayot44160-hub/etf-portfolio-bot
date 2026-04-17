"""
Couche d'abstraction broker — interface commune pour tous les courtiers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from typing import Optional


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


@dataclass
class BrokerOrder:
    ticker: str
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    order_id: Optional[str] = None
    status: str = "pending"
    filled_price: Optional[float] = None
    filled_at: Optional[str] = None


@dataclass
class BrokerPosition:
    ticker: str
    shares: float
    avg_price: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    currency: str = "EUR"


@dataclass
class BrokerAccount:
    broker_name: str
    account_id: str
    cash: float
    total_value: float
    currency: str
    positions: list = field(default_factory=list)
    connected: bool = False


class BaseBroker(ABC):
    """Interface commune pour tous les brokers."""

    def __init__(self, credentials: dict):
        self.credentials = credentials
        self._connected = False

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @property
    def connected(self) -> bool:
        return self._connected

    @abstractmethod
    def connect(self) -> bool:
        """Se connecte au broker. Retourne True si succès."""
        pass

    @abstractmethod
    def disconnect(self):
        """Déconnexion du broker."""
        pass

    @abstractmethod
    def get_account(self) -> BrokerAccount:
        """Récupère les infos du compte (cash, valeur totale, positions)."""
        pass

    @abstractmethod
    def get_positions(self) -> list[BrokerPosition]:
        """Récupère toutes les positions ouvertes."""
        pass

    @abstractmethod
    def get_portfolio_value(self) -> float:
        """Retourne la valeur totale du portefeuille."""
        pass

    @abstractmethod
    def _place_order_impl(self, order: BrokerOrder) -> BrokerOrder:
        """Implémentation broker-spécifique. NE PAS appeler directement — passer par place_order()."""
        pass

    def place_order(self, order: BrokerOrder) -> BrokerOrder:
        """
        Place un ordre. Intercepte via kill-switch + paper mode global.
        - Si kill-switch actif : rejette.
        - Si paper_mode : simule via paper_broker.
        - Sinon : délègue à _place_order_impl (broker réel).
        """
        try:
            from settings import is_kill_switch_active, is_paper_mode, load_settings
        except Exception:
            return self._place_order_impl(order)

        if is_kill_switch_active():
            reason = load_settings().get("kill_switch_reason", "")
            order.status = f"rejected:kill_switch:{reason}"
            return order

        if is_paper_mode():
            try:
                import paper_broker
                from market_data import get_current_prices
                # Prix de référence : limit_price si fourni, sinon dernier prix marché
                px = order.limit_price
                if not px:
                    prices = get_current_prices()
                    px = prices.get(order.ticker, 0) or 0
                if not px:
                    order.status = "rejected:paper_no_price"
                    return order
                sim = paper_broker.simulate_order(
                    symbol=order.ticker,
                    side=order.side.value if hasattr(order.side, "value") else str(order.side),
                    quantity=order.quantity,
                    price=px,
                    order_type=(order.order_type.value if hasattr(order.order_type, "value") else "market").lower(),
                    broker=self.name,
                )
                order.order_id = sim.get("id")
                order.status = sim.get("status", "filled")
                order.filled_price = px
                order.filled_at = sim.get("timestamp")
                try:
                    from notifications import notify_trade_opened
                    notify_trade_opened(order.ticker, order.side.value, px, order.quantity, 0, 0, paper=True)
                except Exception:
                    pass
                return order
            except Exception as e:
                order.status = f"rejected:paper_error:{e}"
                return order

        # Mode LIVE
        return self._place_order_impl(order)

    @abstractmethod
    def get_order_status(self, order_id: str) -> BrokerOrder:
        """Vérifie le statut d'un ordre."""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Annule un ordre. Retourne True si succès."""
        pass

    def buy(self, ticker: str, quantity: float, order_type: OrderType = OrderType.MARKET,
            limit_price: float = None) -> BrokerOrder:
        """Raccourci pour placer un ordre d'achat."""
        order = BrokerOrder(
            ticker=ticker,
            side=OrderSide.BUY,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
        )
        return self.place_order(order)

    def sell(self, ticker: str, quantity: float, order_type: OrderType = OrderType.MARKET,
             limit_price: float = None) -> BrokerOrder:
        """Raccourci pour placer un ordre de vente."""
        order = BrokerOrder(
            ticker=ticker,
            side=OrderSide.SELL,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
        )
        return self.place_order(order)
