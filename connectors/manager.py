"""
Manager des connecteurs actifs.

Garde en memoire les instances connectees, expose des methodes pour
connect/disconnect/list/aggregate. Pour l'instant en memoire pure ;
la persistence (chiffree) est traitee en Phase 3.
"""

from threading import RLock
from typing import Optional
from uuid import uuid4

from connectors.base import BaseConnector, Position
from connectors.registry import get_connector, get_connector_metadata


class ConnectorManager:
    """Singleton en memoire qui gere les connecteurs actifs.

    Une "active connection" = un BaseConnector instancie + connecte.
    Identifie par un account_id genere (uuid4). Permet d'avoir plusieurs
    instances du meme connecteur (ex: 2 comptes IBKR distincts).
    """

    def __init__(self):
        self._lock = RLock()
        self._active: dict[str, BaseConnector] = {}  # account_id -> connector

    # ─── Connect / Disconnect ───────────────────────────────

    def connect(self, connector_id: str, credentials: dict) -> dict:
        """Instancie + connecte un connecteur. Retourne un dict de statut."""
        meta = get_connector_metadata(connector_id)
        if meta is None:
            return {"ok": False, "error": f"Connecteur inconnu: {connector_id}"}

        try:
            conn = get_connector(connector_id, credentials)
        except Exception as e:
            return {"ok": False, "error": f"Instanciation echouee: {e}"}

        try:
            ok = bool(conn.connect())
        except Exception as e:
            return {"ok": False, "error": f"Connexion echouee: {e}"}

        if not ok:
            return {"ok": False, "error": "Connexion refusee par le broker"}

        # Genere un account_id unique
        account_id = conn.account_id or f"{connector_id}-{uuid4().hex[:8]}"

        with self._lock:
            self._active[account_id] = conn

        return {
            "ok": True,
            "account_id": account_id,
            "connector_id": connector_id,
            "name": meta.name,
            "asset_class": meta.asset_class.value,
        }

    def disconnect(self, account_id: str) -> dict:
        with self._lock:
            conn = self._active.pop(account_id, None)
        if conn is None:
            return {"ok": False, "error": "account_id inconnu"}
        try:
            conn.disconnect()
        except Exception:
            pass
        return {"ok": True, "account_id": account_id}

    # ─── Etat ───────────────────────────────────────────────

    def list_active(self) -> list[dict]:
        """Liste les comptes actuellement connectes (metadata legere)."""
        out = []
        with self._lock:
            for account_id, conn in self._active.items():
                meta = conn.METADATA
                out.append({
                    "account_id": account_id,
                    "connector_id": meta.id,
                    "name": meta.name,
                    "asset_class": meta.asset_class.value,
                    "asset_class_label": meta.asset_class.label,
                    "connected": conn.connected,
                })
        return out

    def get(self, account_id: str) -> Optional[BaseConnector]:
        with self._lock:
            return self._active.get(account_id)

    # ─── Donnees agregees ───────────────────────────────────

    def all_positions(self) -> list[dict]:
        """Retourne toutes les positions de tous les comptes connectes,
        serialisees en dict.
        """
        out = []
        with self._lock:
            connectors = list(self._active.items())
        for account_id, conn in connectors:
            try:
                positions = conn.get_positions()
            except Exception as e:
                print(f"[manager] erreur get_positions {account_id}: {e}")
                continue
            for p in positions:
                out.append(_position_to_dict(p, account_id))
        return out

    def aggregated_summary(self) -> dict:
        """Resume agrege : total, repartition par classe d'actif, comptes."""
        positions = []
        accounts = []
        total_value = 0.0
        by_class: dict[str, float] = {}

        with self._lock:
            connectors_snapshot = list(self._active.items())

        for account_id, conn in connectors_snapshot:
            meta = conn.METADATA
            account_value = 0.0
            account_positions_count = 0
            try:
                cash = float(conn.get_balance())
            except Exception:
                cash = 0.0
            try:
                pos_list = conn.get_positions()
            except Exception:
                pos_list = []

            for p in pos_list:
                positions.append(_position_to_dict(p, account_id))
                account_value += p.value
                account_positions_count += 1
                key = p.asset_class.value
                by_class[key] = by_class.get(key, 0.0) + p.value

            account_total = account_value + cash
            total_value += account_total

            # Le cash compte aussi dans by_class (en CASH)
            if cash > 0:
                by_class["cash"] = by_class.get("cash", 0.0) + cash

            accounts.append({
                "account_id": account_id,
                "connector_id": meta.id,
                "name": meta.name,
                "asset_class": meta.asset_class.value,
                "asset_class_label": meta.asset_class.label,
                "cash": cash,
                "positions_value": account_value,
                "total_value": account_total,
                "positions_count": account_positions_count,
                "connected": conn.connected,
            })

        # Conversion by_class avec labels
        from connectors.base import AssetClass
        breakdown = []
        for key, val in by_class.items():
            try:
                ac = AssetClass(key)
                label = ac.label
            except ValueError:
                label = key
            breakdown.append({
                "asset_class": key,
                "asset_class_label": label,
                "value": val,
                "weight_pct": (val / total_value * 100) if total_value else 0,
            })
        breakdown.sort(key=lambda x: x["value"], reverse=True)

        return {
            "total_value": total_value,
            "accounts": accounts,
            "positions": positions,
            "breakdown_by_class": breakdown,
            "currency": "EUR",  # Simplification : tout en EUR (1$ ~= 1€)
        }


def _position_to_dict(p: Position, account_id: str) -> dict:
    """Serialise une Position pour l'API JSON."""
    return {
        "ticker": p.ticker,
        "name": p.name,
        "asset_class": p.asset_class.value,
        "asset_class_label": p.asset_class.label,
        "quantity": p.quantity,
        "avg_price": p.avg_price,
        "current_price": p.current_price,
        "value": p.value,
        "currency": p.currency,
        "unrealized_pnl": p.unrealized_pnl,
        "unrealized_pnl_pct": p.unrealized_pnl_pct,
        "connector_id": p.connector_id,
        "account_id": p.account_id or account_id,
        "sub_account": p.sub_account,
    }


# Singleton global
_MANAGER: Optional[ConnectorManager] = None


def get_manager() -> ConnectorManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = ConnectorManager()
    return _MANAGER
