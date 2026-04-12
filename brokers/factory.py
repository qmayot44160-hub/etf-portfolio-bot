"""
Factory pour instancier le bon broker à partir de la config.
"""

from brokers.base import BaseBroker

BROKER_REGISTRY = {
    "ibkr": {
        "class_path": "brokers.ibkr.IBKRBroker",
        "name": "Interactive Brokers",
        "description": "Trading via TWS/IB Gateway. Paper ou Live.",
        "required_fields": ["host", "port", "client_id"],
        "optional_fields": [],
        "install": "pip install ib_insync",
    },
    "degiro": {
        "class_path": "brokers.degiro.DegiroBroker",
        "name": "Degiro",
        "description": "Broker européen low-cost. Populaire en France.",
        "required_fields": ["username", "password"],
        "optional_fields": ["totp_secret"],
        "install": "pip install degiro-connector",
    },
    "alpaca": {
        "class_path": "brokers.alpaca_broker.AlpacaBroker",
        "name": "Alpaca",
        "description": "Broker US gratuit avec API moderne. Paper trading disponible.",
        "required_fields": ["api_key", "secret_key"],
        "optional_fields": ["paper"],
        "install": "pip install alpaca-trade-api",
    },
    "mexc": {
        "class_path": "brokers.mexc.MEXCBroker",
        "name": "MEXC (Crypto)",
        "description": "Exchange crypto MEXC — spot trading via API.",
        "required_fields": ["api_key", "secret_key"],
        "optional_fields": [],
        "install": "pip install ccxt",
    },
}


def get_broker(broker_id: str, credentials: dict) -> BaseBroker:
    """Instancie un broker à partir de son identifiant."""
    if broker_id not in BROKER_REGISTRY:
        raise ValueError(f"Broker inconnu: {broker_id}. Disponibles: {list(BROKER_REGISTRY.keys())}")

    info = BROKER_REGISTRY[broker_id]
    module_path, class_name = info["class_path"].rsplit(".", 1)

    try:
        import importlib
        module = importlib.import_module(module_path)
        broker_class = getattr(module, class_name)
        return broker_class(credentials)
    except ImportError as e:
        raise ImportError(
            f"Dépendance manquante pour {info['name']}. "
            f"Installez avec: {info['install']}\nErreur: {e}"
        )


def list_brokers() -> list[dict]:
    """Liste tous les brokers disponibles avec leurs infos."""
    return [
        {
            "id": broker_id,
            "name": info["name"],
            "description": info["description"],
            "required_fields": info["required_fields"],
            "optional_fields": info["optional_fields"],
        }
        for broker_id, info in BROKER_REGISTRY.items()
    ]
