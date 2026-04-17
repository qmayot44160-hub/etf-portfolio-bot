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
