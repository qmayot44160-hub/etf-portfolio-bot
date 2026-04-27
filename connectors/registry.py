"""
Registry des connecteurs disponibles.

Deux modes de declaration :
1. Decorateur @register_connector sur la classe
2. Enregistrement manuel via register_connector(MaClasseConnector)

Le registry est rempli au premier appel a list_connectors() ou get_connector(),
qui declenche la decouverte automatique des modules dans connectors/.
"""

import importlib
import pkgutil
from typing import Optional

from connectors.base import BaseConnector, ConnectorMetadata


# Registre global : id -> classe
CONNECTOR_REGISTRY: dict[str, type[BaseConnector]] = {}

# Flag pour ne faire la decouverte qu'une fois
_DISCOVERY_DONE = False


def register_connector(cls: type[BaseConnector]) -> type[BaseConnector]:
    """Decorateur pour enregistrer un connecteur.

    Usage :

        @register_connector
        class MEXCConnector(BaseConnector):
            METADATA = ConnectorMetadata(id="mexc", ...)
    """
    if not issubclass(cls, BaseConnector):
        raise TypeError(f"{cls.__name__} doit heriter de BaseConnector")
    if cls.METADATA is None:
        raise ValueError(f"{cls.__name__} doit definir METADATA")

    connector_id = cls.METADATA.id
    if connector_id in CONNECTOR_REGISTRY and CONNECTOR_REGISTRY[connector_id] is not cls:
        # Permet de re-importer en dev sans crasher, mais previent les collisions reelles
        existing = CONNECTOR_REGISTRY[connector_id]
        if existing.__module__ != cls.__module__:
            raise ValueError(
                f"Conflit d'id de connecteur '{connector_id}' : "
                f"{existing.__module__}.{existing.__name__} vs "
                f"{cls.__module__}.{cls.__name__}"
            )
    CONNECTOR_REGISTRY[connector_id] = cls
    return cls


def _discover_connectors() -> None:
    """Importe tous les modules dans connectors/ pour declencher les decorateurs.

    Appelle automatiquement par list_connectors / get_connector au premier usage.
    """
    global _DISCOVERY_DONE
    if _DISCOVERY_DONE:
        return
    import connectors as _pkg
    for _, mod_name, _ in pkgutil.iter_modules(_pkg.__path__):
        # Skip les modules systeme
        if mod_name.startswith("_") or mod_name in ("base", "registry"):
            continue
        try:
            importlib.import_module(f"connectors.{mod_name}")
        except ImportError:
            # Dependance optionnelle manquante : on log mais on continue
            # (un user peut vouloir utiliser MEXC sans avoir installe ib_insync)
            pass
        except Exception as e:
            # On ne veut pas qu'un connecteur cassé empeche le reste de marcher
            import traceback
            print(f"[connectors] erreur au chargement de {mod_name}: {e}")
            traceback.print_exc()
    _DISCOVERY_DONE = True


def list_connectors(asset_class: Optional[str] = None) -> list[dict]:
    """Liste tous les connecteurs disponibles avec leurs metadata.

    Args:
        asset_class: si fourni, filtre par classe principale.

    Retourne une liste de dict serialisable pour l'API.
    """
    _discover_connectors()
    metas = [cls.METADATA.to_dict() for cls in CONNECTOR_REGISTRY.values()]
    if asset_class:
        metas = [m for m in metas if m["asset_class"] == asset_class]
    return sorted(metas, key=lambda m: (m.get("beta", False), m["name"]))


def get_connector_metadata(connector_id: str) -> Optional[ConnectorMetadata]:
    """Retourne la metadata d'un connecteur sans l'instancier."""
    _discover_connectors()
    cls = CONNECTOR_REGISTRY.get(connector_id)
    return cls.METADATA if cls else None


def get_connector(connector_id: str, credentials: dict) -> BaseConnector:
    """Instancie un connecteur a partir de son id.

    Raises :
        ValueError si l'id est inconnu
        ImportError si une dependance optionnelle manque
    """
    _discover_connectors()
    cls = CONNECTOR_REGISTRY.get(connector_id)
    if cls is None:
        available = list(CONNECTOR_REGISTRY.keys())
        raise ValueError(
            f"Connecteur inconnu: '{connector_id}'. Disponibles: {available}"
        )
    return cls(credentials)
