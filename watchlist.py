"""
Module Watchlist — suivi d'actifs sans les détenir.

- Stockage JSON persistant avec verrou thread
- Alertes de seuil (prix au-dessus / en-dessous) → notif Telegram
- Background checker lancé au démarrage (5 min d'intervalle)
- Multi-classes : ETF, Actions, Crypto, Matières premières
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from data_paths import data_path

WATCHLIST_FILE = data_path("watchlist.json")
ALERT_COOLDOWN_HOURS = 6  # Évite spam : 1 alerte max toutes les 6h par ticker+direction

_LOCK = threading.Lock()
_CHECKER_THREAD: Optional[threading.Thread] = None
_CHECKER_STOP = threading.Event()


# ──────────────────────────────────────────────────────────
#  Persistance
# ──────────────────────────────────────────────────────────

def _load() -> Dict[str, dict]:
    if not os.path.exists(WATCHLIST_FILE):
        return {}
    try:
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(items: Dict[str, dict]) -> None:
    try:
        with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2)
    except Exception as e:
        print(f"[watchlist] save error: {e}")


def _key(ticker: str) -> str:
    return (ticker or "").strip().upper()


# ──────────────────────────────────────────────────────────
#  API publique
# ──────────────────────────────────────────────────────────

def list_items() -> List[dict]:
    """Retourne la liste complète enrichie avec le prix courant."""
    with _LOCK:
        items = _load()
    out = []
    # Enrichissement prix
    tickers = list(items.keys())
    prices = _fetch_prices(tickers)
    for k, it in items.items():
        enriched = dict(it)
        price_data = prices.get(k) or {}
        enriched["ticker"] = k
        enriched["price"] = price_data.get("price")
        enriched["change_1d"] = price_data.get("change_1d")
        enriched["change_7d"] = price_data.get("change_7d")
        enriched["change_30d"] = price_data.get("change_30d")
        enriched["sparkline"] = price_data.get("sparkline")
        out.append(enriched)
    # Tri : ajoutés les plus récents en premier
    out.sort(key=lambda x: x.get("added_at", ""), reverse=True)
    return out


def add(ticker: str, name: str = "", asset_class: str = "etf",
        category: str = "") -> dict:
    """Ajoute un ticker à la watchlist."""
    k = _key(ticker)
    if not k:
        return {"ok": False, "error": "Ticker vide"}
    with _LOCK:
        items = _load()
        if k in items:
            return {"ok": True, "already": True, "item": items[k]}
        items[k] = {
            "ticker": k,
            "name": name or k,
            "asset_class": asset_class or "etf",
            "category": category or "",
            "added_at": datetime.utcnow().isoformat() + "Z",
            "alerts": {"above": None, "below": None},
            "last_alert": {},
        }
        _save(items)
        return {"ok": True, "added": True, "item": items[k]}


def remove(ticker: str) -> dict:
    k = _key(ticker)
    with _LOCK:
        items = _load()
        if k not in items:
            return {"ok": False, "error": "Non présent"}
        del items[k]
        _save(items)
    return {"ok": True, "removed": True}


def set_alert(ticker: str, above: Optional[float] = None,
              below: Optional[float] = None) -> dict:
    """Définit les seuils d'alerte. Passer None efface le seuil."""
    k = _key(ticker)
    with _LOCK:
        items = _load()
        if k not in items:
            return {"ok": False, "error": "Non présent"}
        alerts = items[k].get("alerts", {})
        # "clear" explicite si valeur = null
        if above is not None:
            alerts["above"] = float(above) if above else None
        if below is not None:
            alerts["below"] = float(below) if below else None
        # Reset last_alert pour que l'alerte puisse re-déclencher
        items[k]["alerts"] = alerts
        items[k]["last_alert"] = {}
        _save(items)
    return {"ok": True, "alerts": alerts}


def clear_alert(ticker: str) -> dict:
    k = _key(ticker)
    with _LOCK:
        items = _load()
        if k not in items:
            return {"ok": False}
        items[k]["alerts"] = {"above": None, "below": None}
        items[k]["last_alert"] = {}
        _save(items)
    return {"ok": True}


def is_watched(ticker: str) -> bool:
    k = _key(ticker)
    with _LOCK:
        return k in _load()


# ──────────────────────────────────────────────────────────
#  Récupération des prix (yfinance)
# ──────────────────────────────────────────────────────────

def _yf_symbol(ticker: str, asset_class: str = "etf") -> str:
    t = ticker.upper()
    if asset_class == "crypto" and not t.endswith("-USD"):
        base = t.replace("USDT", "").replace("USD", "").replace("/", "")
        return f"{base}-USD"
    return t


def _fetch_prices(tickers: List[str]) -> Dict[str, dict]:
    """Retourne {ticker: {price, change_1d, change_7d, change_30d, sparkline}}."""
    if not tickers:
        return {}
    try:
        import yfinance as yf
    except ImportError:
        return {}

    with _LOCK:
        items = _load()

    # Mapping ticker originaux → symboles yfinance
    yf_map = {}
    for t in tickers:
        it = items.get(t, {})
        yf_sym = _yf_symbol(t, it.get("asset_class", "etf"))
        yf_map[t] = yf_sym

    unique_yf = list(set(yf_map.values()))
    try:
        data = yf.download(
            tickers=unique_yf, period="1mo", interval="1d",
            progress=False, auto_adjust=True, threads=True,
        )
    except Exception as e:
        print(f"[watchlist] yf.download error: {e}")
        return {}

    out = {}
    if data is None or len(data) == 0:
        return {}

    # Extraction Close
    try:
        if hasattr(data.columns, "levels"):
            closes_df = data["Close"]
        else:
            # single ticker
            closes_df = data[["Close"]]
            closes_df.columns = [unique_yf[0]]
    except Exception:
        return {}

    for orig_ticker, yf_sym in yf_map.items():
        try:
            if yf_sym not in closes_df.columns:
                continue
            series = closes_df[yf_sym].dropna()
            if len(series) == 0:
                continue
            current = float(series.iloc[-1])
            # Variations
            chg_1d = chg_7d = chg_30d = None
            if len(series) >= 2:
                chg_1d = (current - float(series.iloc[-2])) / float(series.iloc[-2]) * 100
            if len(series) >= 7:
                chg_7d = (current - float(series.iloc[-7])) / float(series.iloc[-7]) * 100
            if len(series) >= 20:
                chg_30d = (current - float(series.iloc[0])) / float(series.iloc[0]) * 100
            # Sparkline : 20 derniers points
            spark = [round(float(v), 4) for v in series.iloc[-20:].tolist()]
            out[orig_ticker] = {
                "price": round(current, 4),
                "change_1d": round(chg_1d, 2) if chg_1d is not None else None,
                "change_7d": round(chg_7d, 2) if chg_7d is not None else None,
                "change_30d": round(chg_30d, 2) if chg_30d is not None else None,
                "sparkline": spark,
            }
        except Exception:
            continue

    return out


# ──────────────────────────────────────────────────────────
#  Background alerts checker
# ──────────────────────────────────────────────────────────

def _check_alerts_once() -> None:
    """Vérifie tous les seuils et notifie via Telegram si déclenchement."""
    with _LOCK:
        items = _load()
    if not items:
        return

    tickers = list(items.keys())
    prices = _fetch_prices(tickers)
    if not prices:
        return

    now = datetime.utcnow()
    changed = False

    with _LOCK:
        items = _load()  # relecture fraîche
        for k, p in prices.items():
            if k not in items:
                continue
            it = items[k]
            alerts = it.get("alerts") or {}
            above = alerts.get("above")
            below = alerts.get("below")
            if above is None and below is None:
                continue
            price = p.get("price")
            if price is None:
                continue

            last = it.get("last_alert") or {}
            triggered_direction = None
            if above is not None and price >= above:
                triggered_direction = "above"
            elif below is not None and price <= below:
                triggered_direction = "below"

            if not triggered_direction:
                continue

            # Cooldown
            last_ts = last.get(triggered_direction)
            if last_ts:
                try:
                    last_dt = datetime.fromisoformat(last_ts.rstrip("Z"))
                    if now - last_dt < timedelta(hours=ALERT_COOLDOWN_HOURS):
                        continue
                except Exception:
                    pass

            # Notif
            try:
                import notifications as notif
                threshold = above if triggered_direction == "above" else below
                arrow = "🔼" if triggered_direction == "above" else "🔽"
                msg = (
                    f"{arrow} <b>Alerte Watchlist</b>\n\n"
                    f"<b>{k}</b> — {it.get('name') or k}\n"
                    f"Prix actuel : <b>{price}</b>\n"
                    f"Seuil {triggered_direction} : {threshold}\n"
                    f"Variation 1j : {p.get('change_1d', 0):+.2f}%"
                )
                notif.notify(msg, "info", force=True)
            except Exception as e:
                print(f"[watchlist] notif error: {e}")

            # Marque
            last[triggered_direction] = now.isoformat() + "Z"
            it["last_alert"] = last
            items[k] = it
            changed = True

        if changed:
            _save(items)


def _checker_loop(interval_sec: int = 300) -> None:
    # Petit délai initial pour laisser l'app démarrer
    time.sleep(20)
    while not _CHECKER_STOP.is_set():
        try:
            _check_alerts_once()
        except Exception as e:
            print(f"[watchlist] checker error: {e}")
        _CHECKER_STOP.wait(interval_sec)


def start_background_checker(interval_sec: int = 300) -> None:
    global _CHECKER_THREAD
    if _CHECKER_THREAD and _CHECKER_THREAD.is_alive():
        return
    _CHECKER_STOP.clear()
    _CHECKER_THREAD = threading.Thread(
        target=_checker_loop, args=(interval_sec,), daemon=True
    )
    _CHECKER_THREAD.start()
    print(f"[watchlist] background checker started (every {interval_sec}s)")


def stop_background_checker() -> None:
    _CHECKER_STOP.set()
