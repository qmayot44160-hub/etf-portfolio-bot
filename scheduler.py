"""
Scheduler automatique — DCA mensuel + rééquilibrage.
"""

import json
import os
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from data_paths import data_path

SCHEDULER_CONFIG_FILE = data_path("scheduler_config.json")
_scheduler = None
_bot_engine = None


def get_scheduler():
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler()
        _scheduler.start()
    return _scheduler


def load_scheduler_config() -> dict:
    if os.path.exists(SCHEDULER_CONFIG_FILE):
        with open(SCHEDULER_CONFIG_FILE, "r") as f:
            return json.load(f)
    return {
        "dca_enabled": False,
        "dca_day": 1,        # jour du mois
        "dca_hour": 9,
        "dca_amount": 500,
        "rebalance_enabled": False,
        "rebalance_day": "mon",  # jour de la semaine
        "rebalance_hour": 10,
        "last_dca": None,
        "last_rebalance": None,
        "logs": [],
    }


def save_scheduler_config(config: dict):
    with open(SCHEDULER_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2, default=str)


def _log_event(config: dict, event_type: str, message: str):
    config["logs"].append({
        "timestamp": datetime.now().isoformat(),
        "type": event_type,
        "message": message,
    })
    # Garder les 100 derniers logs
    config["logs"] = config["logs"][-100:]
    save_scheduler_config(config)


def run_auto_dca():
    """Exécute un DCA automatique via le broker connecté."""
    from bot_engine import BotEngine
    config = load_scheduler_config()

    try:
        engine = BotEngine()
        if not engine.broker or not engine.broker.connected:
            _log_event(config, "DCA", "ECHEC - Aucun broker connecté")
            return

        results = engine.execute_dca(amount=config["dca_amount"])
        config["last_dca"] = datetime.now().isoformat()
        _log_event(config, "DCA", f"OK - {len(results)} ordres passés pour {config['dca_amount']}EUR")
    except Exception as e:
        _log_event(config, "DCA", f"ECHEC - {str(e)}")


def run_auto_rebalance():
    """Exécute un rééquilibrage automatique via le broker connecté."""
    from bot_engine import BotEngine
    config = load_scheduler_config()

    try:
        engine = BotEngine()
        if not engine.broker or not engine.broker.connected:
            _log_event(config, "REBALANCE", "ECHEC - Aucun broker connecté")
            return

        results = engine.execute_rebalance()
        config["last_rebalance"] = datetime.now().isoformat()
        _log_event(config, "REBALANCE", f"OK - {len(results)} ordres passés")
    except Exception as e:
        _log_event(config, "REBALANCE", f"ECHEC - {str(e)}")


def setup_scheduled_jobs(config: dict = None):
    """Configure les jobs planifiés selon la config."""
    if config is None:
        config = load_scheduler_config()

    scheduler = get_scheduler()

    # Supprimer les jobs existants
    for job_id in ["auto_dca", "auto_rebalance", "daily_backup"]:
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass

    # DCA mensuel
    if config.get("dca_enabled"):
        scheduler.add_job(
            run_auto_dca,
            CronTrigger(day=config["dca_day"], hour=config["dca_hour"], minute=0),
            id="auto_dca",
            name=f"DCA mensuel ({config['dca_amount']}EUR le {config['dca_day']} du mois)",
            replace_existing=True,
        )

    # Rééquilibrage hebdomadaire
    if config.get("rebalance_enabled"):
        scheduler.add_job(
            run_auto_rebalance,
            CronTrigger(day_of_week=config["rebalance_day"], hour=config["rebalance_hour"], minute=0),
            id="auto_rebalance",
            name="Rééquilibrage hebdomadaire",
            replace_existing=True,
        )

    # Backup quotidien (activé par défaut, désactivable via config)
    if config.get("backup_enabled", True):
        from backups import daily_backup_job
        hour = config.get("backup_hour", 3)
        scheduler.add_job(
            daily_backup_job,
            CronTrigger(hour=hour, minute=30),
            id="daily_backup",
            name=f"Backup quotidien ({hour:02d}:30)",
            replace_existing=True,
        )

    save_scheduler_config(config)
    return config


def get_scheduled_jobs() -> list:
    """Retourne la liste des jobs planifiés."""
    scheduler = get_scheduler()
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
        })
    return jobs
