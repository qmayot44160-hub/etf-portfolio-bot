"""
Flux de sentiment crypto - Fear & Greed Index (alternative.me, gratuit, sans cle).

Premier pas vers l'IA n6 (Analyse du Sentiment) des docs APEX.
L'index agrege volatilite, momentum, volume, reseaux sociaux, dominance BTC et
tendances -> un score 0 (peur extreme) a 100 (avidite extreme). Sentiment GLOBAL
du marche crypto (complementaire du sentiment par symbole calcule sur les prix).

Mis a jour une fois par jour cote source -> cache agressif (1h).
Echec silencieux : renvoie available=False, aucune exception propagee.
"""

import time
import requests

_FNG_URL = "https://api.alternative.me/fng/"
_CACHE = {"ts": 0.0, "data": None}
_TTL = 3600  # 1h


def get_fear_greed() -> dict:
    """
    Index Fear & Greed crypto courant.
    Retour : {"available": bool, "value": int 0-100, "label": str, "label_fr": str}
    """
    now = time.time()
    if _CACHE["data"] is not None and (now - _CACHE["ts"]) < _TTL:
        return _CACHE["data"]
    try:
        r = requests.get(_FNG_URL, timeout=5, params={"limit": 1})
        r.raise_for_status()
        item = (r.json().get("data") or [])[0]
        value = int(item["value"])
        data = {
            "available": True,
            "value": value,
            "label": item.get("value_classification", ""),
            "label_fr": _fr_label(value),
        }
        _CACHE["ts"] = now
        _CACHE["data"] = data
        return data
    except Exception:
        # Conserve le dernier cache si on en a un, sinon indisponible
        return _CACHE["data"] if _CACHE["data"] is not None else {"available": False}


def _fr_label(v: int) -> str:
    if v >= 75:
        return "Avidite extreme"
    if v >= 55:
        return "Avidite"
    if v >= 45:
        return "Neutre"
    if v >= 25:
        return "Peur"
    return "Peur extreme"
