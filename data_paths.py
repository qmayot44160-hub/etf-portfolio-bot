"""
Module de résolution des chemins de données persistantes.

Sur Railway, monte un volume à /app/data et définit DATA_DIR=/app/data.
En local, le dossier courant est utilisé (rétrocompatible).

Tous les modules DOIVENT importer data_path() au lieu de hardcoder les noms de fichiers.
"""

import os

# Chemin du volume persistant. Configurable via env var.
# Railway : définir DATA_DIR=/app/data dans Variables + monter un volume à /app/data
# Local : laisse vide → utilise le dossier courant (rétrocompatible)
DATA_DIR = os.environ.get("DATA_DIR", "").strip()

if DATA_DIR:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception as e:
        print(f"[data_paths] WARN : impossible de créer {DATA_DIR} ({e}). Fallback sur dossier courant.")
        DATA_DIR = ""


# ════════════════════════════════════════════════════════════════════
# Multi-user readiness — voir CLAUDE.md / docs internes.
#
# Aujourd'hui PortfolioQuant est mono-utilisateur : un seul login défini
# via APP_USERNAME / APP_PASSWORD côté Railway. Toutes les données vivent
# à plat dans DATA_DIR (ou dossier courant en local).
#
# Quand on basculera en multi-user, tous les fichiers user-specific
# devront vivre dans DATA_DIR/users/{user_id}/. Pour que la transition
# soit indolore, TOUTE NOUVELLE feature qui persiste de la donnée user
# doit utiliser user_data_path() au lieu de data_path() — même si l'ID
# n'est pas encore réel. Le jour J, on changera get_current_user_id()
# pour retourner le vrai ID de session, et tout sera scopé d'un coup.
# ════════════════════════════════════════════════════════════════════

# Sentinelle pour le user "par défaut" (mono-user actuel).
# Le jour du multi-user, get_current_user_id() lira session['user_id']
# et retournera l'ID réel. Les anciens fichiers à plat seront migrés
# vers DATA_DIR/users/default/ par le code de migration.
DEFAULT_USER_ID = "default"


def get_current_user_id() -> str:
    """
    Retourne l'ID utilisateur actuel.

    AUJOURD'HUI : retourne toujours DEFAULT_USER_ID (mono-user).
    DEMAIN : lira `flask.session.get('user_id', DEFAULT_USER_ID)` quand
    on aura activé le multi-user.

    Importé sans dépendance Flask pour rester utilisable depuis n'importe
    quel module (scheduler, bot_engine, etc.) qui tourne hors contexte HTTP.
    """
    # Tentative best-effort d'attraper la session si on est dans un contexte Flask.
    try:
        from flask import session, has_request_context
        if has_request_context():
            uid = session.get("user_id")
            if uid:
                return str(uid)
    except Exception:
        pass
    return DEFAULT_USER_ID


def user_data_path(filename: str, user_id: str | None = None) -> str:
    """
    Chemin d'un fichier de données USER-SPECIFIC.

    À utiliser pour TOUTES les nouvelles fonctionnalités qui persistent
    de la donnée par utilisateur (portefeuille, profil, credentials,
    préférences, etc.).

    Aujourd'hui : retourne data_path("users/default/{filename}").
    Demain (multi-user) : retourne data_path("users/{user_id}/{filename}").

    Le code de migration tournera une fois pour déplacer les fichiers
    legacy à plat vers users/default/ — d'où l'importance d'utiliser
    user_data_path() dès maintenant pour les nouveaux fichiers.

    Exemple :
        path = user_data_path("custom_alerts.json")
        # → DATA_DIR/users/default/custom_alerts.json
    """
    uid = user_id if user_id is not None else get_current_user_id()
    rel = os.path.join("users", uid, filename)
    if not DATA_DIR:
        # En local sans DATA_DIR : on crée quand même la structure
        full = rel
    else:
        full = os.path.join(DATA_DIR, "users", uid, filename)
    # Crée le dossier parent si absent
    parent = os.path.dirname(full) if DATA_DIR else os.path.join("users", uid)
    try:
        os.makedirs(parent, exist_ok=True)
    except Exception:
        pass
    return full


def data_path(filename: str) -> str:
    """
    Retourne le chemin absolu d'un fichier de données.
    - Si DATA_DIR est défini : DATA_DIR/filename
    - Sinon : filename (dossier courant, rétrocompatible)

    Migration transparente : si DATA_DIR est activé mais que le fichier
    existe encore à l'emplacement legacy (dossier courant), on le migre.
    """
    if not DATA_DIR:
        return filename

    new_path = os.path.join(DATA_DIR, filename)
    legacy_path = filename

    # Migration one-shot : copie legacy → new_path si le fichier cible n'existe pas
    if os.path.exists(legacy_path) and not os.path.exists(new_path):
        try:
            import shutil
            shutil.copy2(legacy_path, new_path)
            print(f"[data_paths] Migré {legacy_path} → {new_path}")
        except Exception as e:
            print(f"[data_paths] Migration {legacy_path} échouée : {e}")

    return new_path
