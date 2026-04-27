"""
Wrapper qui expose les brokers legacy (brokers/) comme connecteurs universels.

Permet de continuer a utiliser ibkr, mexc, degiro, alpaca, paper sans les
reecrire, tout en les rendant accessibles via l'API connecteurs unifiee.

Une fois Phase 2/3 terminee, on pourra reecrire chaque broker directement
en BaseConnector et supprimer ce wrapper.
"""

from decimal import Decimal
from typing import Optional

from brokers.factory import BROKER_REGISTRY as LEGACY_REGISTRY
from connectors.base import (
    AssetClass,
    BaseConnector,
    ConnectorCapabilities,
    ConnectorField,
    ConnectorMetadata,
    Position,
)
from connectors.registry import register_connector


# ════════════════════════════════════════════════════════════
# Mapping broker_id -> AssetClass + metadata UI
# ════════════════════════════════════════════════════════════

# Asset class principale par broker (pour les filtres UI)
BROKER_ASSET_CLASS = {
    "ibkr":   AssetClass.EQUITY,
    "degiro": AssetClass.EQUITY,
    "alpaca": AssetClass.EQUITY,
    "mexc":   AssetClass.CRYPTO,
    "paper":  AssetClass.EQUITY,
}

# Indique si le broker est utilisable en hosted (sans installation locale)
BROKER_HOSTED_OK = {
    "ibkr":   False,  # Necessite TWS/IB Gateway local
    "degiro": True,
    "alpaca": True,
    "mexc":   True,
    "paper":  True,
}

# Helpers pour creer les ConnectorField a partir des required/optional du legacy
_FIELD_LABELS = {
    "host":         ("Host", "127.0.0.1", "Adresse IP du TWS/IB Gateway local"),
    "port":         ("Port", "7497", "7497 pour Paper, 7496 pour Live"),
    "client_id":    ("Client ID", "1", "ID unique pour cette connexion"),
    "username":     ("Identifiant", "", ""),
    "password":     ("Mot de passe", "", ""),
    "totp_secret":  ("Secret TOTP (2FA)", "", "Pour l'authentification a deux facteurs"),
    "api_key":      ("API Key", "", "Cle API generee chez le broker"),
    "secret_key":   ("Secret Key", "", "Secret API associe"),
    "paper":        ("Mode Paper", "true", "true pour simulation, false pour live"),
}

_FIELD_TYPES = {
    "password":   "password",
    "secret_key": "password",
    "totp_secret": "password",
    "port":       "number",
    "client_id":  "number",
    "paper":      "boolean",
}


def _make_fields(required: list[str], optional: list[str]) -> list[ConnectorField]:
    """Convertit les required/optional fields legacy en ConnectorField."""
    out = []
    for fid in required:
        label, placeholder, help_text = _FIELD_LABELS.get(fid, (fid.title(), "", ""))
        ftype = _FIELD_TYPES.get(fid, "text")
        out.append(ConnectorField(
            id=fid, label=label, type=ftype,
            required=True, placeholder=placeholder, help=help_text,
        ))
    for fid in optional:
        label, placeholder, help_text = _FIELD_LABELS.get(fid, (fid.title(), "", ""))
        ftype = _FIELD_TYPES.get(fid, "text")
        out.append(ConnectorField(
            id=fid, label=label, type=ftype,
            required=False, placeholder=placeholder, help=help_text,
        ))
    return out


# ════════════════════════════════════════════════════════════
# Wrapper generique
# ════════════════════════════════════════════════════════════

class LegacyBrokerConnector(BaseConnector):
    """Wrapper d'un BaseBroker legacy en BaseConnector universel.

    On ne sous-classe pas par broker, on instancie cette classe avec un
    broker_id, et la METADATA est definie au moment de l'enregistrement.
    """

    # Va etre overrid pour chaque broker via _make_subclass
    METADATA: ConnectorMetadata = None  # type: ignore
    _BROKER_ID: str = ""

    def __init__(self, credentials: dict):
        super().__init__(credentials)
        from brokers.factory import get_broker
        try:
            self._broker = get_broker(self._BROKER_ID, credentials)
        except ImportError as e:
            self._broker = None
            self._import_error = str(e)
        else:
            self._import_error = None

    def connect(self) -> bool:
        if self._broker is None:
            return False
        ok = bool(self._broker.connect())
        self._connected = ok
        if ok:
            try:
                acc = self._broker.get_account()
                self._account_id = getattr(acc, "account_id", self._BROKER_ID)
            except Exception:
                self._account_id = self._BROKER_ID
        return ok

    def disconnect(self) -> None:
        if self._broker is not None:
            try:
                self._broker.disconnect()
            except Exception:
                pass
        self._connected = False

    def get_positions(self) -> list[Position]:
        if not self._broker or not self._broker.connected:
            return []
        ac = BROKER_ASSET_CLASS.get(self._BROKER_ID, AssetClass.OTHER)
        out = []
        try:
            for bp in self._broker.get_positions():
                qty = float(getattr(bp, "shares", 0) or 0)
                avg = getattr(bp, "avg_price", None)
                cur = getattr(bp, "current_price", None)
                value = float(getattr(bp, "market_value", 0) or 0)
                pnl = getattr(bp, "unrealized_pnl", None)
                pnl_pct = None
                if pnl is not None and avg and qty:
                    cost = avg * qty
                    if cost:
                        pnl_pct = (pnl / cost) * 100
                out.append(Position(
                    ticker=getattr(bp, "ticker", ""),
                    name=getattr(bp, "ticker", ""),
                    asset_class=ac,
                    quantity=qty,
                    avg_price=avg,
                    current_price=cur,
                    value=value,
                    currency=getattr(bp, "currency", "EUR"),
                    unrealized_pnl=pnl,
                    unrealized_pnl_pct=pnl_pct,
                    connector_id=self._BROKER_ID,
                    account_id=self._account_id,
                ))
        except Exception as e:
            print(f"[connectors.legacy_brokers] erreur get_positions {self._BROKER_ID}: {e}")
        return out

    def get_balance(self) -> Decimal:
        if not self._broker or not self._broker.connected:
            return Decimal("0")
        try:
            acc = self._broker.get_account()
            return Decimal(str(getattr(acc, "cash", 0) or 0))
        except Exception:
            return Decimal("0")

    def get_total_value(self) -> float:
        if not self._broker or not self._broker.connected:
            return 0.0
        try:
            return float(self._broker.get_portfolio_value())
        except Exception:
            return super().get_total_value()


# ════════════════════════════════════════════════════════════
# Generation dynamique des sous-classes + enregistrement
# ════════════════════════════════════════════════════════════

# Description un peu plus user-friendly que les descriptions legacy
_DESCRIPTIONS_OVERRIDE = {
    "ibkr": (
        "Interactive Brokers - actions, ETF, options. Connexion via TWS/IB Gateway "
        "tournant en local (non hebergeable tel quel). Migration vers Web API REST en cours."
    ),
    "degiro": (
        "Degiro - broker europeen low-cost populaire en France. API officielle limitee, "
        "le connecteur peut etre instable selon les changements cote Degiro."
    ),
    "alpaca": (
        "Alpaca - broker US gratuit avec API moderne. Supporte le paper trading et "
        "les actions/ETF americains."
    ),
    "mexc": (
        "MEXC - exchange crypto. Spot trading, large catalogue d'altcoins. "
        "Cle API generee depuis ton compte MEXC."
    ),
    "paper": (
        "Mode simulation pur - pas de connexion broker reelle, ordres executes au prix marche "
        "courant. Pour tester ta strategie sans risque."
    ),
}


def _make_subclass(broker_id: str, info: dict) -> type[LegacyBrokerConnector]:
    """Cree dynamiquement une sous-classe enregistree pour ce broker_id."""
    asset_class = BROKER_ASSET_CLASS.get(broker_id, AssetClass.OTHER)
    description = _DESCRIPTIONS_OVERRIDE.get(broker_id, info.get("description", ""))
    fields = _make_fields(
        info.get("required_fields", []),
        info.get("optional_fields", []),
    )
    metadata = ConnectorMetadata(
        id=broker_id,
        name=info.get("name", broker_id),
        description=description,
        asset_class=asset_class,
        capabilities=ConnectorCapabilities(
            read_positions=True,
            read_balance=True,
            read_transactions=False,
            place_orders=True,
            cancel_orders=True,
            realtime_prices=False,
        ),
        fields=fields,
        install_hint=info.get("install", ""),
        hosted_compatible=BROKER_HOSTED_OK.get(broker_id, True),
        beta=(broker_id == "degiro"),  # Degiro un peu instable
    )

    # Sous-classe avec _BROKER_ID + METADATA fixes
    cls = type(
        f"Legacy{broker_id.title()}Connector",
        (LegacyBrokerConnector,),
        {"_BROKER_ID": broker_id, "METADATA": metadata},
    )
    return cls


# Enregistre tous les brokers legacy comme connecteurs
for _broker_id, _info in LEGACY_REGISTRY.items():
    _cls = _make_subclass(_broker_id, _info)
    register_connector(_cls)


# Enregistre aussi explicitement le paper broker (n'est pas dans BROKER_REGISTRY
# mais accessible via brokers/paper_broker.py - on skip si pas dispo)
try:
    import paper_broker  # noqa: F401
    # On ne l'enregistre pas via le wrapper car paper_broker n'a pas la meme API.
    # Sera traite en Phase 2.5 avec un connecteur dedie.
except ImportError:
    pass
