"""
Architecture connecteurs universelle de PortfolioQuant.

Un "connecteur" est une integration vers une source de donnees patrimoniale :
broker actions/ETF, exchange crypto, banque PSD2, plateforme de crowdlending,
assurance vie, immo, etc. Tant qu'il y a une API, il y a un connecteur.

Chaque connecteur expose une interface uniforme (BaseConnector) qui retourne
des Position et Transaction normalisees, peu importe la source.

Utilisation typique :

    from connectors import list_connectors, get_connector

    # Lister tout ce qu'on peut connecter
    available = list_connectors()  # [{id, name, asset_class, ...}, ...]

    # Instancier un connecteur avec ses creds
    conn = get_connector('mexc', {'api_key': '...', 'secret_key': '...'})
    conn.connect()
    positions = conn.get_positions()

Pour ajouter un nouveau connecteur, voir CONTRIBUTING.md section
"Ajouter un connecteur".
"""

from connectors.base import (
    AssetClass,
    Position,
    Transaction,
    ConnectorCapabilities,
    ConnectorMetadata,
    ConnectorField,
    BaseConnector,
)
from connectors.registry import (
    register_connector,
    list_connectors,
    get_connector,
    get_connector_metadata,
    CONNECTOR_REGISTRY,
)
from connectors.manager import ConnectorManager, get_manager
from connectors import storage

__all__ = [
    "AssetClass",
    "Position",
    "Transaction",
    "ConnectorCapabilities",
    "ConnectorMetadata",
    "ConnectorField",
    "BaseConnector",
    "register_connector",
    "list_connectors",
    "get_connector",
    "get_connector_metadata",
    "CONNECTOR_REGISTRY",
    "ConnectorManager",
    "get_manager",
    "storage",
]
