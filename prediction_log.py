"""
Journal des prédictions probabilistes - la mémoire du moteur.

Chaque fois que le moteur émet une probabilité (P de hausse), on l'enregistre avec
les conditions du moment (prix, SL, TP, horizon). Plus tard, on réconcilie : le TP
a-t-il été touché avant le SL ? Le résultat réel (0/1) est comparé à la proba prédite.

De ce journal on tire :
- Brier score LIVE (qualité réelle des estimations, pas in-sample)
- Courbe de fiabilité LIVE (le moteur est-il sur/sous-confiant ?)
- L'historique nécessaire au ré-entraînement continu

C'est le "apprendre de ses erreurs" des documents, rendu mesurable.
"""

import json
import os
import time
from datetime import datetime, timezone

from data_paths import user_data_path
from probability_engine import brier_score, reliability_curve

PRED_FILE = "prediction_log.json"

# Statuts d'une prédiction
PENDING = "PENDING"      # en attente de résolution
RESOLVED = "RESOLVED"    # TP ou SL touché, ou horizon dépassé


def _path() -> str:
    return user_data_path(PRED_FILE)


def _load() -> list:
    try:
        p = _path()
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save(records: list):
    try:
        # On borne la taille du journal (2000 dernières)
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(records[-2000:], f)
    except Exception as e:
        print(f"[prediction_log] save error: {e}")


def log_prediction(
    symbol: str,
    prob_up: float,
    entry: float,
    sl: float,
    tp: float,
    signal: str = "",
    horizon_hours: float = None,
    timeframe: str = "",
    shadow: bool = False,
    dedup: bool = False,
) -> str:
    """
    Enregistre une prédiction. Retourne son id ("" si dédupliqué).
    entry/sl/tp servent à réconcilier plus tard (TP avant SL = succès).

    shadow : prédiction "fantôme" (le modèle prédit mais aucun trade n'est ouvert)
             → sert à accumuler de la calibration sans avoir à trader.
    dedup  : si True, ne rien logger tant qu'une prédiction du même symbole est
             PENDING (évite de re-logger le même setup à chaque cycle).
    """
    records = _load()
    if dedup and any(r["symbol"] == symbol and r["status"] == PENDING for r in records):
        return ""
    pid = f"{symbol.replace('/', '')}-{int(time.time()*1000)}"
    records.append({
        "id": pid,
        "symbol": symbol,
        "timeframe": timeframe,
        "signal": signal,
        "prob_up": round(float(prob_up), 4),
        "entry": float(entry),
        "sl": float(sl),
        "tp": float(tp),
        "horizon_hours": horizon_hours,
        "shadow": shadow,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_ts": time.time(),
        "status": PENDING,
        "outcome": None,       # 1 = TP avant SL, 0 = SL avant TP
        "resolved_at": None,
        "resolved_price": None,
    })
    _save(records)
    return pid


def reconcile(price_fetcher, max_horizon_hours: float = 168.0) -> dict:
    """
    Réconcilie les prédictions PENDING.

    price_fetcher(symbol) -> (current_price, high_since, low_since) OU juste
    current_price (float). Si seul le prix courant est fourni, on résout par
    comparaison simple au prix courant (touché TP/SL au prix actuel).

    max_horizon_hours : au-delà, une prédiction non résolue est classée par la
    direction du prix courant vs entry (évite les PENDING éternels).
    """
    records = _load()
    resolved = 0
    now = time.time()
    # Mémoïse le prix par symbole sur ce cycle : plusieurs prédictions PENDING
    # partagent souvent le même symbole → une seule requête ticker au lieu de N.
    price_cache = {}

    for r in records:
        if r["status"] != PENDING:
            continue
        symbol = r["symbol"]
        if symbol in price_cache:
            px = price_cache[symbol]
        else:
            try:
                px = price_fetcher(symbol)
            except Exception:
                px = None
            price_cache[symbol] = px
        if px is None:
            continue

        # price_fetcher peut retourner un tuple (cur, high, low) ou un float
        if isinstance(px, (tuple, list)):
            cur, hi, lo = px[0], px[1], px[2]
        else:
            cur = hi = lo = float(px)
        if cur is None:
            continue

        entry, sl, tp = r["entry"], r["sl"], r["tp"]
        is_long = tp >= entry  # sens du trade déduit du TP

        outcome = None
        if is_long:
            if lo <= sl:
                outcome = 0
            elif hi >= tp:
                outcome = 1
        else:  # short
            if hi >= sl:
                outcome = 0
            elif lo <= tp:
                outcome = 1

        # Timeout : horizon dépassé sans résolution → direction finale
        age_h = (now - r.get("created_ts", now)) / 3600.0
        if outcome is None and age_h >= max_horizon_hours:
            if is_long:
                outcome = 1 if cur > entry else 0
            else:
                outcome = 1 if cur < entry else 0

        if outcome is not None:
            r["status"] = RESOLVED
            r["outcome"] = int(outcome)
            r["resolved_at"] = datetime.now(timezone.utc).isoformat()
            r["resolved_price"] = round(float(cur), 6)
            resolved += 1

    if resolved:
        _save(records)

    pending = sum(1 for r in records if r["status"] == PENDING)
    return {"resolved": resolved, "pending": pending, "total": len(records)}


def calibration_report() -> dict:
    """
    Métriques de calibration LIVE calculées sur les prédictions résolues.
    C'est la mesure honnête de la qualité du moteur sur données réelles.
    """
    records = _load()
    resolved = [r for r in records if r["status"] == RESOLVED and r["outcome"] is not None]
    pending = sum(1 for r in records if r["status"] == PENDING)

    if len(resolved) < 5:
        return {
            "ready": False,
            "n_resolved": len(resolved),
            "n_pending": pending,
            "n_total": len(records),
            "message": "Pas assez de prédictions résolues (minimum 5)",
        }

    probs = [r["prob_up"] for r in resolved]
    outcomes = [r["outcome"] for r in resolved]

    brier = brier_score(probs, outcomes)
    curve = reliability_curve(probs, outcomes)
    win_rate = sum(outcomes) / len(outcomes)
    # Accuracy directionnelle : proba > 0.5 prédit hausse
    correct = sum(1 for p, o in zip(probs, outcomes) if (p >= 0.5) == (o == 1))
    accuracy = correct / len(outcomes)
    avg_prob = sum(probs) / len(probs)

    # Brier de référence (proba constante = taux de base) pour le skill score
    base = win_rate
    brier_ref = sum((base - o) ** 2 for o in outcomes) / len(outcomes)
    skill = 1 - (brier / brier_ref) if brier_ref > 0 else 0.0

    return {
        "ready": True,
        "n_resolved": len(resolved),
        "n_pending": pending,
        "n_total": len(records),
        "brier_score": round(brier, 4),
        "brier_skill_score": round(skill, 4),   # >0 = mieux que proba constante
        "accuracy": round(accuracy, 4),
        "win_rate": round(win_rate, 4),
        "avg_predicted_prob": round(avg_prob, 4),
        "reliability_curve": curve,
    }


def recent_predictions(limit: int = 50) -> list:
    """Dernières prédictions (résolues et en attente), plus récentes d'abord."""
    records = _load()
    return list(reversed(records[-limit:]))


def resolved_training_data() -> tuple:
    """
    Extrait (features non stockées ici, donc renvoie probs/outcomes seulement).
    Pour le ré-entraînement basé features, voir probability_engine.build_training_set.
    Ici on fournit juste le matériel de calibration.
    """
    records = _load()
    resolved = [r for r in records if r["status"] == RESOLVED and r["outcome"] is not None]
    probs = [r["prob_up"] for r in resolved]
    outcomes = [r["outcome"] for r in resolved]
    return probs, outcomes


def reset_log():
    """Efface le journal (repartir de zéro)."""
    _save([])
