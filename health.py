"""
Module de monitoring santé du bot.

3 mécanismes complémentaires :
1. Heartbeat interne : chaque hit sur /api/* met à jour last_seen_at.
2. Daily ping Telegram : notification résumée chaque jour (bot vivant + stats).
3. Startup notification : alerte à chaque (re)démarrage = détection de crashes Railway.
4. /health endpoint public (no auth) : utilisable par UptimeRobot / Railway alerts.
"""

import os
import json
import time
from datetime import datetime, timedelta
from data_paths import data_path

HEALTH_FILE = data_path("health.json")


def _default() -> dict:
    return {
        "last_seen_at": None,        # dernière interaction API
        "started_at": None,          # dernier démarrage
        "startup_count": 0,          # nombre de démarrages (crash detector)
        "last_heartbeat_sent": None, # dernier heartbeat Telegram envoyé
        "errors_last_24h": 0,
        "trades_last_24h": 0,
    }


def _load() -> dict:
    if os.path.exists(HEALTH_FILE):
        try:
            with open(HEALTH_FILE, "r") as f:
                data = json.load(f)
            merged = _default()
            merged.update(data)
            return merged
        except Exception:
            pass
    return _default()


def _save(data: dict):
    try:
        with open(HEALTH_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[Health] save error: {e}")


# ─────────────────────────────────────────────────────────
#  HEARTBEAT / PING
# ─────────────────────────────────────────────────────────

def touch():
    """À appeler à chaque requête - met à jour last_seen_at."""
    data = _load()
    data["last_seen_at"] = datetime.now().isoformat(timespec="seconds")
    _save(data)


def record_startup():
    """Enregistre un démarrage + envoie notification Telegram."""
    data = _load()
    data["started_at"] = datetime.now().isoformat(timespec="seconds")
    data["startup_count"] = data.get("startup_count", 0) + 1
    _save(data)
    try:
        from notifications import notify
        notify(
            f"🟢 <b>Bot démarré</b>\n\n"
            f"Démarrage #{data['startup_count']}\n"
            f"Heure : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Environnement : {'Railway' if os.environ.get('RAILWAY_ENVIRONMENT') else 'Local'}",
            category="info", force=True,
        )
    except Exception as e:
        print(f"[Health] startup notify error: {e}")


def record_error():
    data = _load()
    data["errors_last_24h"] = data.get("errors_last_24h", 0) + 1
    _save(data)


def record_trade():
    data = _load()
    data["trades_last_24h"] = data.get("trades_last_24h", 0) + 1
    _save(data)


# ─────────────────────────────────────────────────────────
#  STATUS / HEALTH CHECK
# ─────────────────────────────────────────────────────────

def get_status() -> dict:
    """
    Retourne un statut de santé complet.
    """
    data = _load()
    now = datetime.now()
    last_seen = data.get("last_seen_at")
    started = data.get("started_at")

    # Calcul uptime depuis le dernier start
    uptime_seconds = None
    if started:
        try:
            uptime_seconds = int((now - datetime.fromisoformat(started)).total_seconds())
        except Exception:
            pass

    # Détection de santé : si last_seen > 10min, on est probablement mort (ou idle complet)
    health = "healthy"
    if last_seen:
        try:
            delta = now - datetime.fromisoformat(last_seen)
            if delta > timedelta(minutes=30):
                health = "stale"
        except Exception:
            health = "unknown"
    else:
        health = "never_seen"

    return {
        "health": health,
        "last_seen_at": last_seen,
        "started_at": started,
        "startup_count": data.get("startup_count", 0),
        "uptime_seconds": uptime_seconds,
        "uptime_human": _humanize_seconds(uptime_seconds) if uptime_seconds else None,
        "errors_last_24h": data.get("errors_last_24h", 0),
        "trades_last_24h": data.get("trades_last_24h", 0),
        "last_heartbeat_sent": data.get("last_heartbeat_sent"),
        "timestamp": now.isoformat(timespec="seconds"),
        "environment": "Railway" if os.environ.get("RAILWAY_ENVIRONMENT") else "Local",
    }


def _humanize_seconds(s: int) -> str:
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60}m"
    return f"{s // 86400}j {(s % 86400) // 3600}h"


# ─────────────────────────────────────────────────────────
#  DAILY HEARTBEAT (job scheduler)
# ─────────────────────────────────────────────────────────

def daily_heartbeat_job():
    """Envoie un résumé quotidien sur Telegram + reset compteurs 24h."""
    try:
        data = _load()
        status = get_status()

        # Résumé avec trades/erreurs des dernières 24h
        msg = (
            f"💓 <b>Heartbeat quotidien</b>\n\n"
            f"Santé : <b>{status['health']}</b>\n"
            f"Uptime : {status['uptime_human'] or 'N/A'}\n"
            f"Démarrages : {status['startup_count']}\n"
            f"Trades (24h) : {status['trades_last_24h']}\n"
            f"Erreurs (24h) : {status['errors_last_24h']}\n"
            f"Env : {status['environment']}"
        )
        try:
            from notifications import notify
            notify(msg, category="daily_summary")
        except Exception as e:
            print(f"[Health] heartbeat notify error: {e}")

        # Reset compteurs quotidiens
        data["last_heartbeat_sent"] = datetime.now().isoformat(timespec="seconds")
        data["errors_last_24h"] = 0
        data["trades_last_24h"] = 0
        _save(data)

        return {"status": "ok", "message": "Heartbeat envoyé"}
    except Exception as e:
        print(f"[Health] daily heartbeat failed: {e}")
        return {"status": "error", "message": str(e)}
