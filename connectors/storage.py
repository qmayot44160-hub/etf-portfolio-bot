"""
Persistance des connecteurs actifs - credentials chiffres Fernet.

Le manager garde les connecteurs en memoire pour les performances, mais
ce module sauvegarde l'etat sur disque a chaque connect/disconnect, et
permet de reconnecter automatiquement au demarrage de l'app.

Format du fichier (data_path('connectors_state.json')) :

    {
      "version": 1,
      "accounts": [
        {
          "account_id": "manual-abc12345",
          "connector_id": "manual",
          "credentials_enc": "gAAAAAB...",   # chiffre Fernet
          "saved_at": "2026-04-27T18:00:00"
        },
        ...
      ]
    }
"""

import json
import os
from datetime import datetime
from threading import RLock

from data_paths import data_path
from security import encrypt_value, decrypt_value


_STORAGE_FILE = "connectors_state.json"
_STORAGE_VERSION = 1
_LOCK = RLock()


def _path() -> str:
    return data_path(_STORAGE_FILE)


def _load_raw() -> dict:
    """Charge le fichier brut (avec creds toujours chiffrees)."""
    p = _path()
    if not os.path.exists(p):
        return {"version": _STORAGE_VERSION, "accounts": []}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"version": _STORAGE_VERSION, "accounts": []}
        if "accounts" not in data:
            data["accounts"] = []
        return data
    except (json.JSONDecodeError, OSError):
        # Si le fichier est corrompu, on repart d'un etat propre plutot
        # que de crasher l'app au demarrage
        return {"version": _STORAGE_VERSION, "accounts": []}


def _save_raw(data: dict) -> None:
    """Ecrit le fichier brut (les creds doivent deja etre chiffrees)."""
    p = _path()
    os.makedirs(os.path.dirname(p), exist_ok=True) if os.path.dirname(p) else None
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)


# ════════════════════════════════════════════════════════════
# API publique
# ════════════════════════════════════════════════════════════

def save_account(account_id: str, connector_id: str, credentials: dict) -> None:
    """Persiste un compte connecte (chiffre les credentials).

    Si l'account_id existe deja, ses credentials sont mis a jour.
    """
    creds_json = json.dumps(credentials, ensure_ascii=False)
    creds_enc = encrypt_value(creds_json)
    entry = {
        "account_id": account_id,
        "connector_id": connector_id,
        "credentials_enc": creds_enc,
        "saved_at": datetime.utcnow().isoformat() + "Z",
    }
    with _LOCK:
        data = _load_raw()
        # Remove any existing entry with same account_id
        data["accounts"] = [a for a in data["accounts"] if a.get("account_id") != account_id]
        data["accounts"].append(entry)
        _save_raw(data)


def remove_account(account_id: str) -> None:
    """Supprime un compte de la persistance."""
    with _LOCK:
        data = _load_raw()
        before = len(data["accounts"])
        data["accounts"] = [a for a in data["accounts"] if a.get("account_id") != account_id]
        if len(data["accounts"]) != before:
            _save_raw(data)


def list_saved_accounts() -> list[dict]:
    """Retourne la liste des comptes persistes (sans dechiffrer les creds).

    Utile pour les vues admin / debug.
    """
    data = _load_raw()
    return [
        {
            "account_id": a.get("account_id"),
            "connector_id": a.get("connector_id"),
            "saved_at": a.get("saved_at"),
            "has_credentials": bool(a.get("credentials_enc")),
        }
        for a in data.get("accounts", [])
    ]


def load_account_credentials(account_id: str) -> dict | None:
    """Dechiffre et retourne les credentials d'un compte. None si introuvable."""
    data = _load_raw()
    for a in data.get("accounts", []):
        if a.get("account_id") == account_id:
            try:
                creds_json = decrypt_value(a["credentials_enc"])
                return json.loads(creds_json)
            except Exception as e:
                print(f"[connectors.storage] erreur dechiffrement {account_id}: {e}")
                return None
    return None


def restore_all() -> list[dict]:
    """Lit tous les comptes persistes, dechiffre et retourne la liste complete.

    Format : [{account_id, connector_id, credentials, saved_at}, ...]
    """
    out = []
    data = _load_raw()
    for a in data.get("accounts", []):
        try:
            creds_json = decrypt_value(a["credentials_enc"])
            credentials = json.loads(creds_json)
        except Exception as e:
            print(f"[connectors.storage] skip {a.get('account_id')}: dechiffrement KO ({e})")
            continue
        out.append({
            "account_id": a.get("account_id"),
            "connector_id": a.get("connector_id"),
            "credentials": credentials,
            "saved_at": a.get("saved_at"),
        })
    return out
