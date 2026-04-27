"""
Connecteur "Manuel" : permet d'ajouter une position a la main, sans API.

Usage : pour suivre des actifs qui n'ont pas d'API (immo direct, oeuvres d'art,
PEA chez certaines banques sans API, etc.). L'utilisateur saisit ticker + nom +
quantite + valeur estimee. Pas de prix temps reel, pas de transactions.

C'est aussi une demonstration minimaliste de comment ecrire un connecteur.
"""

from decimal import Decimal
from typing import Optional

from connectors.base import (
    AssetClass,
    BaseConnector,
    ConnectorCapabilities,
    ConnectorField,
    ConnectorMetadata,
    Position,
)
from connectors.registry import register_connector


@register_connector
class ManualConnector(BaseConnector):
    """Connecteur manuel : positions saisies a la main.

    Les "credentials" servent ici juste a stocker la liste des positions :
    {
        "asset_class": "real_estate",
        "label": "Mon appart Paris 11",
        "positions": [
            {"ticker": "APPART_PARIS11", "name": "Appart 30m2 Paris 11",
             "quantity": 1, "value": 320000, "currency": "EUR"}
        ]
    }
    """

    METADATA = ConnectorMetadata(
        id="manual",
        name="Saisie manuelle",
        description=(
            "Pour suivre des actifs sans API : immobilier direct, oeuvres d'art, "
            "PEA sans API, livrets, ou tout autre actif que tu mets a jour toi-meme."
        ),
        asset_class=AssetClass.OTHER,
        secondary_asset_classes=[
            AssetClass.REAL_ESTATE,
            AssetClass.CASH,
            AssetClass.LIFE_INSURANCE,
            AssetClass.COMMODITY,
        ],
        capabilities=ConnectorCapabilities(
            read_positions=True,
            read_balance=False,
            read_transactions=False,
            place_orders=False,
            cancel_orders=False,
            realtime_prices=False,
        ),
        fields=[
            ConnectorField(
                id="asset_class",
                label="Classe d'actif",
                type="select",
                options=[
                    {"value": "real_estate", "label": "Immobilier"},
                    {"value": "cash", "label": "Liquidites / Livret"},
                    {"value": "life_insurance", "label": "Assurance vie"},
                    {"value": "commodity", "label": "Matieres premieres"},
                    {"value": "other", "label": "Autre"},
                ],
                default="other",
            ),
            ConnectorField(
                id="label",
                label="Nom du compte",
                placeholder="Ex: Mon appart Paris 11",
                help="Pour t'y retrouver dans la liste de tes connexions.",
            ),
        ],
        install_hint="",  # Aucune dependance externe
        hosted_compatible=True,
        beta=False,
    )

    def connect(self) -> bool:
        # Pas de connexion reseau, juste validation des champs
        if not self.credentials.get("label"):
            return False
        self._connected = True
        self._account_id = self.credentials.get("label", "manual")
        return True

    def disconnect(self) -> None:
        self._connected = False

    def get_positions(self) -> list[Position]:
        if not self._connected:
            return []
        raw_positions = self.credentials.get("positions", [])
        ac_value = self.credentials.get("asset_class", "other")
        try:
            ac = AssetClass(ac_value)
        except ValueError:
            ac = AssetClass.OTHER

        result = []
        for p in raw_positions:
            result.append(Position(
                ticker=p.get("ticker", "MANUAL"),
                name=p.get("name", p.get("ticker", "Position manuelle")),
                asset_class=ac,
                quantity=float(p.get("quantity", 1)),
                avg_price=p.get("avg_price"),
                current_price=p.get("current_price"),
                value=float(p.get("value", 0)),
                currency=p.get("currency", "EUR"),
                connector_id=self.id,
                account_id=self._account_id,
                metadata={"manual": True},
            ))
        return result

    def get_balance(self) -> Decimal:
        # Pas de cash separe pour un connecteur manuel
        return Decimal("0")
