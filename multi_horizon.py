"""
Prévision multi-horizon - l'IA n°9 des documents ("Prévision Temporelle").

Un modèle de probabilité distinct par horizon temporel. Chaque modèle prédit
P(le prix soit plus haut dans N candles), donnant une vue étagée du type :
  1 candle : 54%   ·   6 candles : 61%   ·   24 candles : 68%

Réutilise toute l'infra du moteur probabiliste (mêmes 15 features, même régression
logistique pur numpy, même standardisation). La seule différence : le label est
directionnel (close[i+h] > close[i]) au lieu de TP-avant-SL.

Complémentaire du moteur principal :
- probability_engine : "dois-je prendre CE trade" (TP/SL, décision)
- multi_horizon      : "quelle est la tendance attendue à chaque horizon" (prévision)
"""

import json
import os
import numpy as np
import pandas as pd

from data_paths import user_data_path
from technical_analysis import compute_all_indicators
from probability_engine import (
    LogisticModel, _feature_frame, build_training_set, brier_score,
)

MODELS_FILE = "multi_horizon_models.json"

# Horizons par défaut en nombre de candles. Le libellé temporel dépend du timeframe
# (ex: sur 1h → 1h, 3h, 6h, 12h, 24h, 48h). Calculé côté UI.
DEFAULT_HORIZONS = [1, 3, 6, 12, 24, 48]


class MultiHorizonEngine:
    """Gère N modèles logistiques, un par horizon. Persistance groupée."""

    def __init__(self):
        self.models = {}   # {horizon:int -> LogisticModel}
        self.meta = {}
        self._load()

    def _path(self) -> str:
        return user_data_path(MODELS_FILE)

    def _load(self):
        try:
            p = self._path()
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.models = {
                    int(h): LogisticModel.from_dict(md)
                    for h, md in data.get("models", {}).items()
                }
                self.meta = data.get("meta", {})
        except Exception:
            self.models = {}
            self.meta = {}

    def save(self):
        data = {
            "models": {str(h): m.to_dict() for h, m in self.models.items()},
            "meta": self.meta,
        }
        try:
            with open(self._path(), "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"[multi_horizon] save error: {e}")

    def is_ready(self) -> bool:
        return len(self.models) > 0 and all(
            m.trained and m.is_compatible() for m in self.models.values()
        )

    def train_from_history(
        self, df: pd.DataFrame, symbol: str = "",
        horizons: list = None, timeframe: str = "1h",
    ) -> dict:
        """
        Entraîne un modèle directionnel par horizon.
        Retourne les métriques par horizon (Brier, accuracy, base rate).
        """
        horizons = horizons or DEFAULT_HORIZONS
        # compute_all_indicators ne supprime aucune ligne : len(df) suffit,
        # inutile de calculer tous les indicateurs juste pour un décompte.
        n = len(df)
        if n < 200:
            return {"error": f"Pas assez de données ({n} candles, min 200)"}

        results = []
        new_models = {}
        for h in horizons:
            # Garde-fou : besoin d'assez de candles au-delà de l'horizon
            if n - 60 - h < 50:
                results.append({"horizon": h, "error": "Horizon trop long pour l'historique"})
                continue
            X, y = build_training_set(df, horizon=h, label_mode="direction")
            if len(X) < 50:
                results.append({"horizon": h, "error": "Pas assez d'échantillons"})
                continue

            split = int(len(X) * 0.8)
            X_tr, y_tr = X[:split], y[:split]
            X_te, y_te = X[split:], y[split:]

            try:
                model = LogisticModel().fit(X_tr, y_tr)
            except Exception as e:
                results.append({"horizon": h, "error": str(e)})
                continue

            if len(X_te) >= 5:
                p_te = model.predict_proba(X_te)
                brier = brier_score(p_te, y_te)
                acc = float(((p_te >= 0.5).astype(float) == y_te).mean())
            else:
                brier, acc = None, None

            new_models[h] = model
            results.append({
                "horizon": h,
                "n_samples": int(len(X)),
                "base_rate": round(float(y.mean()), 4),
                "test_brier": round(brier, 4) if brier is not None else None,
                "test_accuracy": round(acc, 4) if acc is not None else None,
            })

        if not new_models:
            return {"error": "Aucun modèle entraîné - vérifier les données"}

        self.models = new_models
        self.meta = {
            "symbol": symbol,
            "timeframe": timeframe,
            "horizons": list(new_models.keys()),
            "n_candles": n,
        }
        self.save()

        return {
            "trained": True,
            "symbol": symbol,
            "timeframe": timeframe,
            "horizons": results,
        }

    def predict(self, df: pd.DataFrame, timeframe: str = "1h") -> dict:
        """
        Prévision directionnelle multi-horizon pour le dernier candle.
        Retourne une liste triée par horizon avec le libellé temporel.
        """
        if not self.is_ready():
            return {"available": False}
        if "rsi" not in df.columns or "atr" not in df.columns:
            df = compute_all_indicators(df.copy())
        feats = _feature_frame(df)
        x = feats.iloc[-1].values

        horizons = []
        for h in sorted(self.models.keys()):
            model = self.models[h]
            if not model.trained:
                continue
            prob = model.predict_proba(x)
            horizons.append({
                "horizon_candles": h,
                "label": _horizon_label(h, timeframe),
                "prob_up": round(prob, 4),
                "prob_down": round(1 - prob, 4),
            })
        return {"available": True, "timeframe": timeframe, "horizons": horizons}

    def status(self) -> dict:
        return {
            "trained": self.is_ready(),
            "n_horizons": len(self.models),
            "meta": self.meta,
        }

    def reset(self):
        self.models = {}
        self.meta = {}
        try:
            p = self._path()
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


# Minutes par candle pour les timeframes usuels
_TF_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "12h": 720,
    "1d": 1440, "1w": 10080,
}


def _horizon_label(candles: int, timeframe: str) -> str:
    """Convertit un nombre de candles en libellé temporel lisible."""
    mins = _TF_MINUTES.get(timeframe, 60) * candles
    if mins < 60:
        return f"{mins}min"
    if mins < 1440:
        h = mins / 60
        return f"{int(h)}h" if h == int(h) else f"{h:.1f}h"
    d = mins / 1440
    return f"{int(d)}j" if d == int(d) else f"{d:.1f}j"
