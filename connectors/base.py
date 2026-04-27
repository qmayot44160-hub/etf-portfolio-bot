"""
Interface universelle pour les connecteurs.

Tout type de source patrimoniale (broker, exchange, banque, crowdlending, etc.)
implemente BaseConnector et expose des Position et Transaction normalisees.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, Any


# ════════════════════════════════════════════════════════════
# CLASSES D'ACTIFS
# ════════════════════════════════════════════════════════════

class AssetClass(str, Enum):
    """Classe d'actif d'un connecteur ou d'une position.

    On herite de str pour que ce soit serialisable JSON directement.
    """
    EQUITY = "equity"            # Actions, ETF, fonds
    CRYPTO = "crypto"            # BTC, ETH, altcoins
    CASH = "cash"                # Compte courant, livrets, especes
    BOND = "bond"                # Obligations
    REAL_ESTATE = "real_estate"  # Immo direct, SCPI
    LENDING = "lending"          # Crowdlending (October, La Premiere Brique)
    LIFE_INSURANCE = "life_insurance"  # Assurance vie (Linxea, etc.)
    COMMODITY = "commodity"      # Or, argent, matieres premieres
    OTHER = "other"              # Tout le reste

    @property
    def label(self) -> str:
        """Label utilisateur en francais."""
        return _ASSET_CLASS_LABELS.get(self.value, self.value)


_ASSET_CLASS_LABELS = {
    "equity": "Actions / ETF",
    "crypto": "Crypto",
    "cash": "Liquidites",
    "bond": "Obligations",
    "real_estate": "Immobilier",
    "lending": "Crowdlending",
    "life_insurance": "Assurance vie",
    "commodity": "Matieres premieres",
    "other": "Autre",
}


# ════════════════════════════════════════════════════════════
# POSITION ET TRANSACTION NORMALISEES
# ════════════════════════════════════════════════════════════

@dataclass
class Position:
    """Une position normalisee, peu importe la source.

    Pour un ETF : ticker = "VWCE", quantity = nb d'actions
    Pour une crypto : ticker = "BTC", quantity = montant
    Pour un livret : ticker = "LIVRET_A", quantity = 1, value = montant
    Pour un bien immo : ticker = "ADRESSE_HASH", quantity = 1, value = estimation
    """
    # Identite
    ticker: str                          # Symbole (BTC, VWCE, LIVRET_A, etc.)
    name: str                            # Nom lisible ("Bitcoin", "Vanguard FTSE All-World")
    asset_class: AssetClass              # Classe d'actif

    # Quantites et valeurs
    quantity: float                      # Nombre d'unites (actions, BTC, etc.)
    avg_price: Optional[float] = None    # Prix moyen d'achat (PRU)
    current_price: Optional[float] = None  # Prix unitaire actuel
    value: float = 0.0                   # Valeur totale actuelle (quantity * current_price ou montant direct)
    currency: str = "EUR"                # Devise

    # PnL
    unrealized_pnl: Optional[float] = None      # Plus/moins-value latente absolue
    unrealized_pnl_pct: Optional[float] = None  # Plus/moins-value latente en %

    # Source
    connector_id: Optional[str] = None   # 'ibkr', 'mexc', 'bridge', etc.
    account_id: Optional[str] = None     # Identifiant compte source
    sub_account: Optional[str] = None    # Sub-compte (ex: PEA / CTO chez le meme broker)

    # Metadata libre (pour stocker des infos specifiques au connecteur)
    metadata: dict = field(default_factory=dict)


@dataclass
class Transaction:
    """Une transaction normalisee."""
    # Identite
    id: str                              # ID unique cote source
    timestamp: datetime                  # Date/heure
    asset_class: AssetClass              # Classe d'actif

    # Mouvement
    side: str                            # "buy", "sell", "deposit", "withdraw", "dividend", "interest", "fee"
    ticker: Optional[str] = None         # Si applicable
    quantity: Optional[float] = None     # Quantite mouvementee
    price: Optional[float] = None        # Prix unitaire au moment du trade
    amount: float = 0.0                  # Montant total (signed : positif = entree, negatif = sortie)
    fee: float = 0.0                     # Frais
    currency: str = "EUR"

    # Source
    connector_id: Optional[str] = None
    account_id: Optional[str] = None

    # Metadata
    metadata: dict = field(default_factory=dict)


# ════════════════════════════════════════════════════════════
# METADATA D'UN CONNECTEUR (pour le registry et l'UI)
# ════════════════════════════════════════════════════════════

@dataclass
class ConnectorCapabilities:
    """Ce que le connecteur sait faire."""
    read_positions: bool = True          # Lire les positions
    read_balance: bool = True            # Lire le solde cash
    read_transactions: bool = False      # Historique des transactions
    place_orders: bool = False           # Executer des ordres
    cancel_orders: bool = False          # Annuler des ordres
    realtime_prices: bool = False        # Prix temps reel via le connecteur


@dataclass
class ConnectorField:
    """Description d'un champ a remplir pour configurer le connecteur.

    Sert a auto-generer le formulaire de connexion cote UI.
    """
    id: str                              # 'api_key', 'host', etc.
    label: str                           # Label affiche a l'utilisateur
    type: str = "text"                   # 'text', 'password', 'number', 'select', 'boolean'
    required: bool = True
    placeholder: str = ""
    help: str = ""                       # Texte d'aide sous le champ
    options: Optional[list[dict]] = None  # Pour type='select' : [{value, label}]
    default: Optional[Any] = None


@dataclass
class ConnectorMetadata:
    """Description statique d'un connecteur (avant instanciation)."""
    id: str                              # 'ibkr', 'mexc', 'bridge', etc.
    name: str                            # 'Interactive Brokers'
    description: str                     # Phrase pour l'utilisateur
    asset_class: AssetClass              # Classe principale
    secondary_asset_classes: list[AssetClass] = field(default_factory=list)
    capabilities: ConnectorCapabilities = field(default_factory=ConnectorCapabilities)
    fields: list[ConnectorField] = field(default_factory=list)
    install_hint: str = ""               # 'pip install ccxt' si la dep est optionnelle
    docs_url: str = ""                   # Lien documentation utilisateur
    logo_url: Optional[str] = None       # Pour l'UI
    hosted_compatible: bool = True       # False si necessite quelque chose en local (ex: TWS pour IBKR)
    beta: bool = False

    def to_dict(self) -> dict:
        """Serialisation JSON-friendly pour l'API."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "asset_class": self.asset_class.value,
            "asset_class_label": self.asset_class.label,
            "secondary_asset_classes": [c.value for c in self.secondary_asset_classes],
            "capabilities": {
                "read_positions": self.capabilities.read_positions,
                "read_balance": self.capabilities.read_balance,
                "read_transactions": self.capabilities.read_transactions,
                "place_orders": self.capabilities.place_orders,
                "cancel_orders": self.capabilities.cancel_orders,
                "realtime_prices": self.capabilities.realtime_prices,
            },
            "fields": [
                {
                    "id": f.id, "label": f.label, "type": f.type,
                    "required": f.required, "placeholder": f.placeholder,
                    "help": f.help, "options": f.options, "default": f.default,
                }
                for f in self.fields
            ],
            "install_hint": self.install_hint,
            "docs_url": self.docs_url,
            "logo_url": self.logo_url,
            "hosted_compatible": self.hosted_compatible,
            "beta": self.beta,
        }


# ════════════════════════════════════════════════════════════
# INTERFACE BASE
# ════════════════════════════════════════════════════════════

class BaseConnector(ABC):
    """Interface universelle pour toute source patrimoniale.

    Pour ajouter un nouveau connecteur :
    1. Heriter de BaseConnector
    2. Definir METADATA (ConnectorMetadata)
    3. Implementer connect, get_positions, get_balance au minimum
    4. Decorer avec @register_connector ou ajouter au registry manuel
    """

    # Doit etre overrid par les sous-classes
    METADATA: ConnectorMetadata = None  # type: ignore

    def __init__(self, credentials: dict):
        if self.METADATA is None:
            raise NotImplementedError(
                f"{self.__class__.__name__} doit definir METADATA (ConnectorMetadata)"
            )
        self.credentials = credentials
        self._connected = False
        self._account_id: Optional[str] = None

    @property
    def id(self) -> str:
        return self.METADATA.id

    @property
    def name(self) -> str:
        return self.METADATA.name

    @property
    def asset_class(self) -> AssetClass:
        return self.METADATA.asset_class

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def account_id(self) -> Optional[str]:
        return self._account_id

    # ─── Methodes obligatoires ──────────────────────────────

    @abstractmethod
    def connect(self) -> bool:
        """Etablit la connexion. Retourne True si succes, False sinon."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Termine proprement la connexion (ferme socket, deconnecte session, etc.)."""
        pass

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Retourne toutes les positions actuelles, normalisees."""
        pass

    @abstractmethod
    def get_balance(self) -> Decimal:
        """Retourne le solde cash disponible (devise principale du compte)."""
        pass

    # ─── Methodes optionnelles avec defaut raisonnable ──────

    def get_total_value(self) -> float:
        """Valeur totale du compte = somme des positions + cash.

        Override si le connecteur expose une valeur totale directement
        (souvent plus precise car evalue par la plateforme elle-meme).
        """
        positions_value = sum(p.value for p in self.get_positions())
        cash = float(self.get_balance())
        return positions_value + cash

    def get_transactions(self, since: Optional[datetime] = None) -> list[Transaction]:
        """Retourne l'historique des transactions depuis `since`.

        Default : raise si capability non supportee.
        """
        if not self.METADATA.capabilities.read_transactions:
            raise NotImplementedError(
                f"{self.name} ne supporte pas la lecture des transactions"
            )
        return []

    def health_check(self) -> dict:
        """Verifie que la connexion est toujours valide.

        Retourne {ok: bool, message: str, latency_ms: int|None}.
        Default : appelle get_balance comme test.
        """
        from time import time
        try:
            t0 = time()
            self.get_balance()
            return {
                "ok": True,
                "message": "OK",
                "latency_ms": int((time() - t0) * 1000),
            }
        except Exception as e:
            return {"ok": False, "message": str(e), "latency_ms": None}

    # ─── Helpers ────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.id} connected={self.connected}>"
