"""
Module de notifications - Telegram.

Envoie des alertes pour :
- Trade ouvert / fermé
- Stop loss / take profit touché
- Erreur critique du bot
- Kill-switch activé
- Résumé quotidien
- Changement de régime de marché
"""

import os
import json
import time
import requests
from datetime import datetime
from data_paths import data_path


NOTIF_CONFIG_FILE = data_path("notifications_config.json")
NOTIF_LOG_FILE = data_path("notifications.log")


def _load_config() -> dict:
    if os.path.exists(NOTIF_CONFIG_FILE):
        try:
            with open(NOTIF_CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "telegram_enabled": False,
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "notify_trades": True,
        "notify_errors": True,
        "notify_kill_switch": True,
        "notify_daily_summary": True,
        "notify_regime_change": True,
    }


def save_config(cfg: dict) -> dict:
    """Merge + persist."""
    from security import encrypt_value, decrypt_value
    current = _load_config()
    # Chiffre le token avant persistance
    if "telegram_bot_token" in cfg and cfg["telegram_bot_token"] and not cfg["telegram_bot_token"].startswith("enc::"):
        cfg["telegram_bot_token"] = "enc::" + encrypt_value(cfg["telegram_bot_token"])
    current.update(cfg)
    with open(NOTIF_CONFIG_FILE, "w") as f:
        json.dump(current, f, indent=2)
    return current


def get_config_safe() -> dict:
    """Config pour l'UI : masque le token (en clair, pas le chiffré)."""
    cfg = _load_config()
    token = cfg.get("telegram_bot_token", "")
    if token:
        # Déchiffre pour afficher les VRAIS 4 derniers caractères (confort UX)
        plain = _decrypt_token(cfg)
        if len(plain) > 4:
            cfg["telegram_bot_token_masked"] = "●●●●●●" + plain[-4:]
        else:
            cfg["telegram_bot_token_masked"] = "●●●●"
        cfg["telegram_bot_token"] = ""  # jamais renvoyé au client
    return cfg


def _decrypt_token(cfg: dict) -> str:
    from security import decrypt_value
    token = cfg.get("telegram_bot_token", "")
    if token.startswith("enc::"):
        return decrypt_value(token[5:])
    return token


# ─────────────────────────────────────────────────────────
#  ENVOI TELEGRAM
# ─────────────────────────────────────────────────────────

def _send_telegram(text: str, parse_mode: str = "HTML") -> bool:
    cfg = _load_config()
    if not cfg.get("telegram_enabled"):
        return False
    token = _decrypt_token(cfg)
    chat_id = cfg.get("telegram_chat_id", "")
    if not token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        r = requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }, timeout=10)
        return r.ok
    except Exception as e:
        print(f"[Notifications] Telegram error: {e}")
        return False


def notify(message: str, category: str = "info", force: bool = False) -> bool:
    """
    Envoie une notification.
    category: trades | errors | kill_switch | daily_summary | regime_change | info
    force: bypass le filtre par catégorie
    """
    cfg = _load_config()
    if not force:
        cat_key = f"notify_{category}"
        if cat_key in cfg and not cfg[cat_key]:
            return False

    # Log local
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(NOTIF_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [{category.upper()}] {message}\n")
    except Exception:
        pass

    return _send_telegram(message)


# ─────────────────────────────────────────────────────────
#  HELPERS SPÉCIFIQUES
# ─────────────────────────────────────────────────────────

def notify_trade_opened(symbol: str, side: str, price: float,
                        quantity: float, sl: float, tp: float,
                        paper: bool = False) -> bool:
    mode = "🧪 PAPER" if paper else "💰 LIVE"
    emoji = "🟢" if side == "BUY" else "🔴"
    msg = (
        f"{emoji} <b>{mode} - Trade ouvert</b>\n\n"
        f"<b>{symbol}</b> · {side}\n"
        f"Prix : {price:.4f}\n"
        f"Quantité : {quantity}\n"
        f"🛑 SL : {sl:.4f}\n"
        f"🎯 TP : {tp:.4f}"
    )
    return notify(msg, "trades")


def notify_trade_closed(symbol: str, side: str, pnl: float, pnl_pct: float,
                        reason: str = "", paper: bool = False) -> bool:
    mode = "🧪 PAPER" if paper else "💰 LIVE"
    emoji = "✅" if pnl >= 0 else "❌"
    msg = (
        f"{emoji} <b>{mode} - Trade fermé</b>\n\n"
        f"<b>{symbol}</b> · {side}\n"
        f"P&L : <b>{pnl:+.2f}$</b> ({pnl_pct:+.2f}%)\n"
        f"Raison : {reason or 'N/A'}"
    )
    return notify(msg, "trades")


def notify_smart_pick(pick: dict) -> bool:
    """
    Alerte Telegram pour un nouveau Smart Pick à haut score.
    pick : dict retourné par etf_scanner (ticker, name, score, direction, reasons…).
    """
    ticker = pick.get("ticker") or pick.get("symbol") or "?"
    name = pick.get("name") or ticker
    score = pick.get("score", 0)
    direction = pick.get("direction", "NEUTRAL")
    chg_30d = pick.get("change_30d", 0)
    asset_class = pick.get("asset_class", "ETF").upper()

    arrow = "📈" if direction == "BULL" else "📉" if direction == "BEAR" else "➡️"
    reasons = pick.get("reasons", [])
    reasons_txt = "\n".join(f"• {r}" for r in reasons[:3]) if reasons else "• Opportunité détectée"

    msg = (
        f"{arrow} <b>Smart Pick détecté</b>\n\n"
        f"<b>{ticker}</b> - {name}\n"
        f"Classe : {asset_class} · Score : <b>{score:.0f}/100</b>\n"
        f"30j : <b>{chg_30d:+.2f}%</b> · Direction : <b>{direction}</b>\n\n"
        f"{reasons_txt}"
    )
    return notify(msg, "info")


def notify_error(message: str, source: str = "") -> bool:
    src = f"Source : {source}\n" if source else ""
    msg = f"⚠️ <b>Erreur bot</b>\n\n{src}{message}"
    return notify(msg, "errors")


def notify_kill_switch(reason: str = "") -> bool:
    msg = (
        f"🚨 <b>KILL-SWITCH ACTIVÉ</b>\n\n"
        f"Toutes les activités du bot ont été stoppées.\n"
        f"Raison : {reason or 'Activation manuelle'}"
    )
    return notify(msg, "kill_switch", force=True)


def notify_daily_summary(stats: dict) -> bool:
    total_pnl = stats.get("total_pnl", 0)
    trades = stats.get("total_trades", 0)
    wr = stats.get("win_rate", 0)
    emoji = "📈" if total_pnl >= 0 else "📉"
    msg = (
        f"{emoji} <b>Résumé quotidien</b>\n\n"
        f"P&L jour : <b>{total_pnl:+.2f}$</b>\n"
        f"Trades : {trades}\n"
        f"Win rate : {wr}%\n"
        f"Date : {datetime.now().strftime('%Y-%m-%d')}"
    )
    return notify(msg, "daily_summary")


def notify_regime_change(old: str, new: str) -> bool:
    msg = f"🌡️ <b>Régime de marché changé</b>\n\n{old} → <b>{new}</b>"
    return notify(msg, "regime_change")


def test_notification() -> dict:
    """Test manuel depuis l'UI."""
    ok = notify(
        f"✅ <b>Test de notification</b>\n\n"
        f"Si tu vois ce message, les notifications Telegram fonctionnent.\n"
        f"Envoyé à {datetime.now().strftime('%H:%M:%S')}",
        "info", force=True
    )
    return {"sent": ok}
