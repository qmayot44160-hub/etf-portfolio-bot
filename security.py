"""
Module de sécurité - authentification + chiffrement des credentials.

Fonctionnalités :
- Authentification par mot de passe (session Flask)
- Chiffrement Fernet (AES-128-CBC + HMAC) des credentials broker
- Clé de chiffrement dérivée d'une variable d'environnement ou fichier local
- Vérification constant-time des mots de passe
"""

import os
import json
import hmac
import hashlib
import secrets
import base64
from functools import wraps
from flask import request, session, jsonify, redirect, url_for
from data_paths import data_path


# ─────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────

APP_PASSWORD_ENV = "APP_PASSWORD"         # Mot de passe de l'app
APP_USERNAME_ENV = "APP_USERNAME"         # Identifiant (optionnel)
FERNET_KEY_ENV = "FERNET_KEY"             # Clé de chiffrement (base64)
SECRET_KEY_ENV = "FLASK_SECRET_KEY"       # Session Flask
KEY_FILE = data_path(".secret_key")       # Fallback local (gitignored)


def get_flask_secret() -> str:
    """Clé Flask session - via env ou fichier local persistant."""
    env = os.environ.get(SECRET_KEY_ENV)
    if env:
        return env
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "r") as f:
            return f.read().strip()
    # Génère une clé persistante locale
    key = secrets.token_urlsafe(32)
    with open(KEY_FILE, "w") as f:
        f.write(key)
    return key


def get_app_password() -> str:
    """Mot de passe de l'app (optionnel). Si vide → auth désactivée."""
    return os.environ.get(APP_PASSWORD_ENV, "").strip()


def get_app_username() -> str:
    """Identifiant de l'app (optionnel). Si vide → aucun check d'identifiant."""
    return os.environ.get(APP_USERNAME_ENV, "").strip()


def is_auth_enabled() -> bool:
    return bool(get_app_password())


def is_username_required() -> bool:
    """True si un APP_USERNAME est défini (donc à saisir côté login)."""
    return bool(get_app_username())


def verify_password(candidate: str) -> bool:
    """Comparaison constant-time du mot de passe."""
    expected = get_app_password()
    if not expected:
        return True
    return hmac.compare_digest(expected.encode(), (candidate or "").encode())


def verify_credentials(username: str, password: str) -> bool:
    """Vérifie (identifiant, mot de passe) en constant-time.

    Si ``APP_USERNAME`` n'est pas défini, l'identifiant est ignoré
    (rétrocompatibilité avec les installations password-only).
    """
    expected_user = get_app_username()
    user_ok = True
    if expected_user:
        user_ok = hmac.compare_digest(expected_user.encode(), (username or "").encode())
    pw_ok = verify_password(password)
    # Eviter un short-circuit qui fuirait du timing
    return user_ok and pw_ok


# ─────────────────────────────────────────────────────────
#  DÉCORATEUR FLASK
# ─────────────────────────────────────────────────────────

def login_required(f):
    """Protège une route Flask. Retourne 401 pour API, redirect pour pages."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not is_auth_enabled():
            return f(*args, **kwargs)
        if session.get("auth_ok"):
            return f(*args, **kwargs)
        # API → JSON 401 ; page → redirect /login
        if request.path.startswith("/api/"):
            return jsonify({"error": "Authentification requise", "auth_required": True}), 401
        return redirect("/login")
    return wrapper


# ─────────────────────────────────────────────────────────
#  CHIFFREMENT - FERNET
# ─────────────────────────────────────────────────────────

def _get_fernet_key() -> bytes:
    """Clé Fernet (44 chars base64). Env var > fichier local > auto-génération."""
    env_key = os.environ.get(FERNET_KEY_ENV, "").strip()
    if env_key:
        return env_key.encode()

    key_path = data_path(".fernet_key")
    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            return f.read().strip()

    # Génère et persiste localement
    try:
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
    except Exception:
        # Fallback si cryptography manquant : dérivation HMAC (moins robuste)
        seed = get_flask_secret().encode()
        key = base64.urlsafe_b64encode(hashlib.sha256(seed).digest())

    with open(key_path, "wb") as f:
        f.write(key)
    return key


def encrypt_value(plaintext: str) -> str:
    """Chiffre une string → base64."""
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_get_fernet_key())
        return f.encrypt(plaintext.encode()).decode()
    except Exception:
        # Fallback XOR masqué (mieux que rien, mais non cryptographique)
        key = hashlib.sha256(_get_fernet_key()).digest()
        data = plaintext.encode()
        masked = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
        return "xor:" + base64.urlsafe_b64encode(masked).decode()


def decrypt_value(ciphertext: str) -> str:
    """Déchiffre une string base64."""
    if not ciphertext:
        return ""
    if ciphertext.startswith("xor:"):
        key = hashlib.sha256(_get_fernet_key()).digest()
        data = base64.urlsafe_b64decode(ciphertext[4:])
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(data)).decode()
    try:
        from cryptography.fernet import Fernet
        f = Fernet(_get_fernet_key())
        return f.decrypt(ciphertext.encode()).decode()
    except Exception:
        return ciphertext  # déjà en clair (migration rétrocompatible)


def encrypt_dict(d: dict, keys: list) -> dict:
    """Chiffre les valeurs sensibles d'un dict (in-place safe)."""
    out = dict(d)
    for k in keys:
        if k in out and isinstance(out[k], str) and out[k]:
            out[k] = "enc::" + encrypt_value(out[k])
    return out


def decrypt_dict(d: dict) -> dict:
    """Déchiffre les valeurs préfixées par 'enc::'."""
    out = dict(d)
    for k, v in out.items():
        if isinstance(v, str) and v.startswith("enc::"):
            out[k] = decrypt_value(v[5:])
    return out
