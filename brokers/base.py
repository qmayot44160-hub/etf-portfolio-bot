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
    quantity: int
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
    def place_order(self, order: BrokerOrder) -> BrokerOrder:
        """Place un ordre. Retourne l'ordre avec son statut mis à jour."""
        pass

    @abstractmethod
    def get_order_status(self, order_id: str) -> BrokerOrder:
        """Vérifie le statut d'un ordre."""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Annule un ordre. Retourne True si succès."""
        pass

    def buy(self, ticker: str, quantity: int, order_type: OrderType = OrderType.MARKET,
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

    def sell(self, ticker: str, quantity: int, order_type: OrderType = OrderType.MARKET,
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
