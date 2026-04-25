"""
Module de backups - snapshot quotidien des fichiers de données critiques.

Crée une archive ZIP datée dans DATA_DIR/backups/ contenant tous les JSON
de l'app. Rétention configurable (par défaut 14 jours).

API :
- create_backup(label=None) -> dict : crée un snapshot immédiat
- list_backups() -> list : liste les backups existants avec taille + date
- restore_backup(backup_id) -> dict : restaure un backup (dangereux, on sauvegarde avant)
- prune_old_backups(keep_days=14) : supprime les backups > keep_days
- delete_backup(backup_id) -> dict : supprime un backup spécifique
"""

import os
import json
import shutil
import zipfile
from datetime import datetime, timedelta
from data_paths import data_path, DATA_DIR

# Dossier racine pour les backups
BACKUP_DIR = data_path("backups")

# Fichiers à inclure dans chaque backup (relatifs à DATA_DIR, ou dossier courant si DATA_DIR vide)
BACKUP_FILES = [
    "portfolio_state.json",
    "broker_config.json",
    "crypto_config.json",
    "trader_config.json",
    "scheduler_config.json",
    "scanner_config.json",
    "scanner_cache.json",
    "risk_config.json",
    "risk_log.json",
    "trades_history.json",
    "active_trades.json",
    "settings.json",
    "notifications_config.json",
    "paper_state.json",
    "paper_trades.json",
]

RETENTION_DAYS = 14


def _ensure_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


def _backup_root() -> str:
    """Dossier où chercher les fichiers source."""
    return DATA_DIR if DATA_DIR else "."


def create_backup(label: str = None) -> dict:
    """
    Crée un backup ZIP daté. Retourne {id, path, size, created_at, files_count}.
    """
    _ensure_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    label_clean = f"_{label}" if label else ""
    backup_id = f"{ts}{label_clean}"
    archive_path = os.path.join(BACKUP_DIR, f"{backup_id}.zip")

    root = _backup_root()
    included = []
    skipped = []

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in BACKUP_FILES:
            src = os.path.join(root, fname)
            if os.path.exists(src):
                zf.write(src, arcname=fname)
                included.append(fname)
            else:
                skipped.append(fname)
        # Manifest meta
        manifest = {
            "backup_id": backup_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "label": label,
            "files": included,
            "skipped": skipped,
        }
        zf.writestr("_manifest.json", json.dumps(manifest, indent=2))

    size = os.path.getsize(archive_path)
    # Auto-prune après création
    try:
        prune_old_backups(RETENTION_DAYS)
    except Exception as e:
        print(f"[Backups] prune error: {e}")

    return {
        "status": "ok",
        "backup_id": backup_id,
        "path": archive_path,
        "size_bytes": size,
        "size_kb": round(size / 1024, 1),
        "created_at": manifest["created_at"],
        "files_count": len(included),
        "skipped_count": len(skipped),
    }


def list_backups() -> list:
    """Retourne la liste des backups triés du plus récent au plus ancien."""
    _ensure_dir()
    out = []
    for fname in os.listdir(BACKUP_DIR):
        if not fname.endswith(".zip"):
            continue
        path = os.path.join(BACKUP_DIR, fname)
        try:
            size = os.path.getsize(path)
            backup_id = fname[:-4]  # strip .zip
            # Lit le manifest pour plus d'info
            meta = {}
            try:
                with zipfile.ZipFile(path, "r") as zf:
                    if "_manifest.json" in zf.namelist():
                        meta = json.loads(zf.read("_manifest.json").decode())
            except Exception:
                pass
            out.append({
                "backup_id": backup_id,
                "filename": fname,
                "size_bytes": size,
                "size_kb": round(size / 1024, 1),
                "created_at": meta.get("created_at") or datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds"),
                "label": meta.get("label"),
                "files_count": len(meta.get("files", [])),
            })
        except Exception as e:
            print(f"[Backups] error reading {fname}: {e}")
    out.sort(key=lambda b: b["created_at"], reverse=True)
    return out


def restore_backup(backup_id: str) -> dict:
    """
    Restaure un backup : extrait tous les fichiers dans DATA_DIR en remplaçant l'existant.
    Crée automatiquement un safety backup AVANT restauration.
    """
    path = os.path.join(BACKUP_DIR, f"{backup_id}.zip")
    if not os.path.exists(path):
        return {"status": "error", "message": f"Backup introuvable : {backup_id}"}

    # Safety backup d'abord
    safety = create_backup(label="pre_restore")

    root = _backup_root()
    restored = []
    errors = []
    try:
        with zipfile.ZipFile(path, "r") as zf:
            for name in zf.namelist():
                if name == "_manifest.json":
                    continue
                try:
                    zf.extract(name, root)
                    restored.append(name)
                except Exception as e:
                    errors.append(f"{name}: {e}")
    except Exception as e:
        return {"status": "error", "message": str(e), "safety_backup": safety["backup_id"]}

    return {
        "status": "ok",
        "restored_files": restored,
        "errors": errors,
        "safety_backup": safety["backup_id"],
        "note": "Un safety backup a été créé avant restauration. Redémarre l'app pour recharger les configs.",
    }


def delete_backup(backup_id: str) -> dict:
    path = os.path.join(BACKUP_DIR, f"{backup_id}.zip")
    if not os.path.exists(path):
        return {"status": "error", "message": "Backup introuvable"}
    try:
        os.remove(path)
        return {"status": "ok", "deleted": backup_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def prune_old_backups(keep_days: int = RETENTION_DAYS) -> dict:
    """Supprime les backups > keep_days."""
    _ensure_dir()
    cutoff = datetime.now() - timedelta(days=keep_days)
    deleted = []
    for fname in os.listdir(BACKUP_DIR):
        if not fname.endswith(".zip"):
            continue
        path = os.path.join(BACKUP_DIR, fname)
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            if mtime < cutoff:
                os.remove(path)
                deleted.append(fname)
        except Exception as e:
            print(f"[Backups] prune error on {fname}: {e}")
    return {"status": "ok", "deleted_count": len(deleted), "deleted": deleted}


def daily_backup_job():
    """Point d'entrée appelé par le scheduler APScheduler."""
    try:
        result = create_backup(label="daily")
        print(f"[Backups] Daily backup OK : {result['backup_id']} ({result['size_kb']} KB)")
        return result
    except Exception as e:
        print(f"[Backups] Daily backup FAILED : {e}")
        try:
            from notifications import notify_error
            notify_error(f"Backup quotidien échoué : {e}", source="backups")
        except Exception:
            pass
        return {"status": "error", "message": str(e)}
