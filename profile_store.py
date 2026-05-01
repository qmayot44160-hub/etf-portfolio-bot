"""
Module de gestion du profil utilisateur.

Stocke les préférences utilisateur (nom, email, avatar, locale, prefs d'affichage,
profil d'investisseur). Persiste dans profile.json.

Bot mono-utilisateur : un seul profil par installation.
"""

import os
import json
import hashlib
from datetime import datetime
from typing import Optional
from data_paths import data_path

PROFILE_FILE = data_path("profile.json")
AUDIT_FILE = data_path("audit_log.json")

AVATAR_COLORS = [
    "#0a84ff",  # blue
    "#30d158",  # green
    "#bf5af2",  # purple
    "#ff9f0a",  # orange
    "#64d2ff",  # teal
    "#ff453a",  # red
    "#ffd60a",  # yellow
    "#ff375f",  # pink
]

VALID_RISK_PROFILES = {"prudent", "équilibré", "dynamique", "agressif"}
VALID_HORIZONS = {"court", "moyen", "long"}  # <3ans / 3-10ans / 10+ans
VALID_EXPERIENCE = {"débutant", "intermédiaire", "avancé", "expert"}
VALID_DENSITY = {"confortable", "standard", "compact"}
VALID_LOCALES = {"fr-FR", "en-US"}
VALID_CURRENCIES = {"USD", "EUR", "GBP", "CHF"}


def _default_profile() -> dict:
    return {
        # Identité
        "display_name": "Investisseur",
        "email": "",
        "avatar_color": "#0a84ff",
        "avatar_initials": "",        # auto-calculées à partir du nom si vide
        "bio": "",
        # Profil investisseur
        "risk_profile": "équilibré",
        "investment_horizon": "long",
        "experience_level": "intermédiaire",
        "target_monthly_dca": 500.0,
        "goals": "",                   # objectifs personnels (texte libre)
        # Localisation
        "locale": "fr-FR",
        "currency": "USD",
        "timezone": "Europe/Paris",
        # Affichage
        "density": "standard",
        "animations_enabled": True,
        "reduce_motion": False,
        # Quieter default (cf. PRODUCT.md "Le calme est un feature").
        # Aurora + grain etaient on par defaut, ce qui contredisait le principe
        # "pas d'animation decorative". L'utilisateur peut les reactiver dans
        # Settings > Apparence si la deco lui manque.
        "show_ambient_aurora": False,
        "show_film_grain": False,
        "number_format": "fr-FR",
        # Méta
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
        "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def _compute_initials(name: str) -> str:
    """'Quentin Mayot' → 'QM', 'Q' → 'Q', '' → 'U'."""
    parts = [p for p in (name or "").strip().split() if p]
    if not parts:
        return "U"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def load_profile() -> dict:
    """Charge le profil (merge avec defaults pour rétrocompatibilité)."""
    if os.path.exists(PROFILE_FILE):
        try:
            with open(PROFILE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged = _default_profile()
            merged.update(data)
            # Toujours recalculer les initiales si vides
            if not merged.get("avatar_initials"):
                merged["avatar_initials"] = _compute_initials(merged.get("display_name", ""))
            return merged
        except Exception as e:
            print(f"[Profile] Load error: {e}")
    # Premier lancement
    prof = _default_profile()
    prof["avatar_initials"] = _compute_initials(prof["display_name"])
    return prof


def save_profile(updates: dict) -> dict:
    """Merge + validate + persist. Retourne le profil complet."""
    current = load_profile()

    # Sanitize / validate each field that's being updated
    if "display_name" in updates:
        name = str(updates["display_name"]).strip()[:60]
        if name:
            current["display_name"] = name
            current["avatar_initials"] = _compute_initials(name)
    if "email" in updates:
        email = str(updates["email"]).strip().lower()[:120]
        current["email"] = email  # validation UI-side (format)
    if "avatar_color" in updates and updates["avatar_color"] in AVATAR_COLORS:
        current["avatar_color"] = updates["avatar_color"]
    if "bio" in updates:
        current["bio"] = str(updates["bio"]).strip()[:280]
    if "risk_profile" in updates and updates["risk_profile"] in VALID_RISK_PROFILES:
        current["risk_profile"] = updates["risk_profile"]
    if "investment_horizon" in updates and updates["investment_horizon"] in VALID_HORIZONS:
        current["investment_horizon"] = updates["investment_horizon"]
    if "experience_level" in updates and updates["experience_level"] in VALID_EXPERIENCE:
        current["experience_level"] = updates["experience_level"]
    if "target_monthly_dca" in updates:
        try:
            current["target_monthly_dca"] = max(0.0, float(updates["target_monthly_dca"]))
        except Exception:
            pass
    if "goals" in updates:
        current["goals"] = str(updates["goals"]).strip()[:500]
    if "locale" in updates and updates["locale"] in VALID_LOCALES:
        current["locale"] = updates["locale"]
    if "currency" in updates and updates["currency"] in VALID_CURRENCIES:
        current["currency"] = updates["currency"]
    if "timezone" in updates:
        current["timezone"] = str(updates["timezone"]).strip()[:60]
    if "density" in updates and updates["density"] in VALID_DENSITY:
        current["density"] = updates["density"]
    for flag in ("animations_enabled", "reduce_motion", "show_ambient_aurora", "show_film_grain"):
        if flag in updates:
            current[flag] = bool(updates[flag])
    if "number_format" in updates and updates["number_format"] in VALID_LOCALES:
        current["number_format"] = updates["number_format"]

    current["updated_at"] = datetime.utcnow().isoformat(timespec="seconds")

    with open(PROFILE_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2, ensure_ascii=False)

    return current


# ─────────────────────────────────────────────────────────
#  AUDIT LOG - journal d'activité simple
# ─────────────────────────────────────────────────────────

def log_event(event_type: str, details: str = "", meta: Optional[dict] = None) -> None:
    """Append un événement au log d'audit (cap à 500 entrées)."""
    try:
        entries = []
        if os.path.exists(AUDIT_FILE):
            with open(AUDIT_FILE, "r", encoding="utf-8") as f:
                entries = json.load(f)
        entries.insert(0, {
            "ts": datetime.utcnow().isoformat(timespec="seconds"),
            "type": event_type,
            "details": details,
            "meta": meta or {},
        })
        entries = entries[:500]
        with open(AUDIT_FILE, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[Profile] audit log error: {e}")


def get_audit_log(limit: int = 100) -> list:
    if os.path.exists(AUDIT_FILE):
        try:
            with open(AUDIT_FILE, "r", encoding="utf-8") as f:
                entries = json.load(f)
            return entries[:limit]
        except Exception:
            pass
    return []


# ─────────────────────────────────────────────────────────
#  EXPORT / PURGE
# ─────────────────────────────────────────────────────────

def full_export() -> dict:
    """Rassemble toutes les données user pour export RGPD."""
    from settings import load_settings
    out = {
        "exported_at": datetime.utcnow().isoformat(timespec="seconds"),
        "profile": load_profile(),
        "settings": load_settings(),
        "audit_log": get_audit_log(500),
    }
    # Ajoute portefeuille si dispo
    try:
        from portfolio import load_state
        out["portfolio_state"] = load_state()
    except Exception:
        pass
    # Ajoute notif config (sans tokens)
    try:
        from notifications import get_config
        cfg = get_config()
        # Masquer tokens sensibles
        if "telegram_bot_token" in cfg:
            cfg["telegram_bot_token"] = "***masked***"
        out["notifications_config"] = cfg
    except Exception:
        pass
    return out


def reset_profile() -> dict:
    """Remet le profil à zéro (garde le fichier, juste défauts)."""
    if os.path.exists(PROFILE_FILE):
        os.remove(PROFILE_FILE)
    log_event("profile_reset", "Profil remis aux valeurs par défaut")
    return load_profile()
