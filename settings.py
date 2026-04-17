"""
Module de configuration globale — kill-switch + mode paper/live.

Persiste dans settings.json. Consulté par tous les modules qui placent des ordres.
"""

import os
import json
import time
from datetime import datetime

SETTINGS_FILE = "settings.json"


def _default() -> dict:
    return {
        "kill_switch": False,
        "kill_switch_reason": "",
        "kill_switch_triggered_at": None,
        "paper_mode": True,   # SECURITÉ : par défaut en paper (pas d'ordre réel)
        "paper_balance_usd": 10000.0,
    }


def load_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
            # merge avec defaults (pour ajouter les nouveaux champs)
            merged = _default()
            merged.update(data)
            return merged
        except Exception:
            pass
    return _default()


def save_settings(cfg: dict) -> dict:
    current = load_settings()
    current.update(cfg)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(current, f, indent=2)
    return current


# ─────────────────────────────────────────────────────────
#  KILL-SWITCH
# ─────────────────────────────────────────────────────────

def is_kill_switch_active() -> bool:
    return bool(load_settings().get("kill_switch"))


def trigger_kill_switch(reason: str = "Activation manuelle") -> dict:
    """Active le kill-switch. Bloque tous les ordres futurs."""
    cfg = save_settings({
        "kill_switch": True,
        "kill_switch_reason": reason,
        "kill_switch_triggered_at": datetime.now().isoformat(timespec="seconds"),
    })
    # Notification Telegram
    try:
        from notifications import notify_kill_switch
        notify_kill_switch(reason)
    except Exception as e:
        print(f"[Settings] Kill-switch notify error: {e}")
    return cfg


def release_kill_switch() -> dict:
    """Désactive le kill-switch. Nécessite action humaine."""
    return save_settings({
        "kill_switch": False,
        "kill_switch_reason": "",
        "kill_switch_triggered_at": None,
    })


# ─────────────────────────────────────────────────────────
#  PAPER TRADING
# ─────────────────────────────────────────────────────────

def is_paper_mode() -> bool:
    return bool(load_settings().get("paper_mode", True))


def set_paper_mode(enabled: bool) -> dict:
    return save_settings({"paper_mode": bool(enabled)})


# ─────────────────────────────────────────────────────────
#  GARDE GLOBALE — À APPELER AVANT CHAQUE ORDRE
# ─────────────────────────────────────────────────────────

def can_trade_live() -> tuple:
    """
    Retourne (ok: bool, reason: str).
    ok=True si on peut envoyer un ordre réel ; False sinon.
    """
    s = load_settings()
    if s.get("kill_switch"):
        return False, f"Kill-switch actif : {s.get('kill_switch_reason', '')}"
    if s.get("paper_mode"):
        return False, "Mode PAPER activé"
    return True, ""
