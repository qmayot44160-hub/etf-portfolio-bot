"""
Moteur probabiliste - transforme le consensus multi-signaux en probabilité calibrée.

Coeur de la vision APEX : au lieu de dire "ACHAT confiance 89%", le moteur estime
"P(le prix gagne tp_mult*ATR avant de perdre sl_mult*ATR dans les N candles) = 74%".

Principe :
- Régression logistique PURE NUMPY (aucune dépendance ajoutée) sur les features
  déjà calculées par technical_analysis.
- Labels générés depuis l'historique : pour chaque candle, on regarde en avant si
  le TP est touché avant le SL (label 1) ou l'inverse (label 0).
- La probabilité produite est ensuite loggée (prediction_log.py) et comparée au
  résultat réel → mesure de calibration (Brier score, courbe de fiabilité).

C'est la brique qui permet "d'apprendre de ses erreurs" : plus le moteur trade,
plus il a de données, mieux il se calibre.
"""

import json
import os
import numpy as np
import pandas as pd

from data_paths import user_data_path
from technical_analysis import compute_all_indicators


MODEL_FILE = "probability_model.json"

# Ordre FIXE des features. Ne jamais réordonner sans réentraîner.
# train et predict utilisent la même extraction → cohérence garantie.
FEATURE_NAMES = [
    "rsi",            # RSI / 100
    "ema9_dev",       # (price/ema_9 - 1) * 100
    "ema21_dev",      # (price/ema_21 - 1) * 100
    "sma50_dev",      # (price/sma_50 - 1) * 100
    "ema200_dev",     # (price/ema_200 - 1) * 100
    "bb_pos",         # position dans les bandes de Bollinger [0=bas, 1=haut]
    "macd_hist",      # (macd - macd_signal) / atr
    "stoch_k",        # stoch_k / 100
    "cmf",            # Chaikin Money Flow
    "adx",            # ADX / 100
    "atr_pct",        # atr / price * 100 (volatilité)
    "obv_slope",      # signe de la variation OBV sur 5 candles
    "squeeze_mom",    # squeeze_momentum / atr
    "ret_1",          # rendement 1 candle (%)
    "ret_5",          # rendement 5 candles (%)
]


def _feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """
    Construit la matrice de features (une ligne par candle) de façon vectorisée.
    Utilisée à l'entraînement (toutes les lignes) et en live (dernière ligne).
    df doit déjà contenir les indicateurs (compute_all_indicators).
    """
    close = df["close"]
    atr = df["atr"].replace(0, np.nan)
    price = close

    feat = pd.DataFrame(index=df.index)
    feat["rsi"] = df["rsi"] / 100.0
    feat["ema9_dev"] = (price / df["ema_9"] - 1) * 100
    feat["ema21_dev"] = (price / df["ema_21"] - 1) * 100
    feat["sma50_dev"] = (price / df["sma_50"] - 1) * 100
    # ema_200 peut être absente si historique court
    if "ema_200" in df.columns:
        feat["ema200_dev"] = (price / df["ema_200"] - 1) * 100
    else:
        feat["ema200_dev"] = 0.0
    bb_range = (df["bb_upper"] - df["bb_lower"]).replace(0, np.nan)
    feat["bb_pos"] = ((close - df["bb_lower"]) / bb_range).clip(-0.5, 1.5)
    feat["macd_hist"] = (df["macd"] - df["macd_signal"]) / atr
    feat["stoch_k"] = df["stoch_k"] / 100.0
    feat["cmf"] = df.get("cmf", pd.Series(0.0, index=df.index))
    feat["adx"] = df.get("adx", pd.Series(0.0, index=df.index)) / 100.0
    feat["atr_pct"] = (atr / price) * 100
    if "obv" in df.columns:
        feat["obv_slope"] = np.sign(df["obv"] - df["obv"].shift(5))
    else:
        feat["obv_slope"] = 0.0
    if "squeeze_momentum" in df.columns:
        feat["squeeze_mom"] = (df["squeeze_momentum"] / atr).fillna(0.0)
    else:
        feat["squeeze_mom"] = 0.0
    feat["ret_1"] = close.pct_change(1) * 100
    feat["ret_5"] = close.pct_change(5) * 100

    # Nettoyage : inf → nan → 0 (les premières lignes ont des nan de warmup)
    feat = feat.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return feat[FEATURE_NAMES]


def build_training_set(
    df: pd.DataFrame,
    sl_mult: float = 1.5,
    tp_mult: float = 3.0,
    horizon: int = 24,
    warmup: int = 60,
    label_mode: str = "tp_sl",
) -> tuple:
    """
    Génère (X, y) depuis l'historique.

    label_mode = "tp_sl" (défaut) : pour chaque candle i,
      TP = close[i] + tp_mult*ATR[i],  SL = close[i] - sl_mult*ATR[i]
      label 1 si le high atteint TP avant que le low atteigne SL dans `horizon`
      candles, 0 si le SL est touché d'abord, sinon direction finale.
      → "proba de gagner X% avant de perdre Y%" (moteur de décision principal).

    label_mode = "direction" : label 1 si close[i+horizon] > close[i].
      → prévision directionnelle pure à l'horizon (module multi-horizon).
    """
    df = compute_all_indicators(df.copy())
    feats = _feature_frame(df)

    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    atr = df["atr"].values
    n = len(df)

    X, y = [], []
    for i in range(warmup, n - 1):
        end = min(i + horizon, n - 1)

        if label_mode == "direction":
            # Prévision directionnelle pure à l'horizon
            label = 1 if close[end] > close[i] else 0
            X.append(feats.iloc[i].values)
            y.append(label)
            continue

        # Mode tp_sl (défaut)
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        entry = close[i]
        tp = entry + tp_mult * a
        sl = entry - sl_mult * a

        label = None
        for j in range(i + 1, end + 1):
            hit_tp = high[j] >= tp
            hit_sl = low[j] <= sl
            if hit_tp and hit_sl:
                # Les deux dans le même candle : on suppose le pire (SL d'abord)
                label = 0
                break
            if hit_sl:
                label = 0
                break
            if hit_tp:
                label = 1
                break
        if label is None:
            # Non résolu dans l'horizon : direction finale
            label = 1 if close[end] > entry else 0

        X.append(feats.iloc[i].values)
        y.append(label)

    if not X:
        return np.array([]), np.array([])
    return np.array(X, dtype=float), np.array(y, dtype=float)


class LogisticModel:
    """
    Régression logistique binaire en pur numpy.
    Standardisation intégrée + régularisation L2 + descente de gradient.
    """

    def __init__(self):
        self.weights = None       # np.array (n_features,)
        self.bias = 0.0
        self.mean = None          # standardisation
        self.std = None
        self.trained = False
        self.n_samples = 0
        self.feature_names = list(FEATURE_NAMES)

    @staticmethod
    def _sigmoid(z):
        # Clip pour éviter overflow
        z = np.clip(z, -30, 30)
        return 1.0 / (1.0 + np.exp(-z))

    def fit(self, X, y, lr=0.1, epochs=400, l2=0.01):
        """Entraîne le modèle. X (n, f), y (n,) binaire."""
        if len(X) < 30:
            raise ValueError(f"Pas assez de données ({len(X)} < 30)")
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)

        # Standardisation (mémorisée pour le predict)
        self.mean = X.mean(axis=0)
        self.std = X.std(axis=0)
        self.std[self.std == 0] = 1.0
        Xs = (X - self.mean) / self.std

        n, f = Xs.shape
        self.weights = np.zeros(f)
        self.bias = 0.0

        for _ in range(epochs):
            z = Xs @ self.weights + self.bias
            p = self._sigmoid(z)
            error = p - y
            grad_w = (Xs.T @ error) / n + l2 * self.weights
            grad_b = error.mean()
            self.weights -= lr * grad_w
            self.bias -= lr * grad_b

        self.trained = True
        self.n_samples = int(n)
        return self

    def predict_proba(self, x):
        """x : vecteur (f,) OU matrice (n, f). Retourne proba(s) [0,1]."""
        if not self.trained:
            raise RuntimeError("Modèle non entraîné")
        x = np.asarray(x, dtype=float)
        single = x.ndim == 1
        if single:
            x = x.reshape(1, -1)
        xs = (x - self.mean) / self.std
        p = self._sigmoid(xs @ self.weights + self.bias)
        return float(p[0]) if single else p

    def feature_importance(self) -> list:
        """Poids standardisés triés par magnitude : quels features pèsent le plus."""
        if not self.trained:
            return []
        imp = sorted(
            zip(self.feature_names, self.weights.tolist()),
            key=lambda kv: abs(kv[1]),
            reverse=True,
        )
        return [{"feature": k, "weight": round(v, 4)} for k, v in imp]

    def to_dict(self) -> dict:
        return {
            "weights": self.weights.tolist() if self.weights is not None else None,
            "bias": float(self.bias),
            "mean": self.mean.tolist() if self.mean is not None else None,
            "std": self.std.tolist() if self.std is not None else None,
            "trained": self.trained,
            "n_samples": self.n_samples,
            "feature_names": self.feature_names,
        }

    @classmethod
    def from_dict(cls, d: dict):
        m = cls()
        if not d or not d.get("trained"):
            return m
        m.weights = np.array(d["weights"], dtype=float)
        m.bias = float(d["bias"])
        m.mean = np.array(d["mean"], dtype=float)
        m.std = np.array(d["std"], dtype=float)
        m.trained = True
        m.n_samples = int(d.get("n_samples", 0))
        m.feature_names = d.get("feature_names", list(FEATURE_NAMES))
        return m


def brier_score(probs, outcomes) -> float:
    """
    Brier score = moyenne des (proba - résultat)². Plus bas = mieux calibré.
    0 = parfait, 0.25 = équivalent au hasard (proba constante 0.5).
    """
    probs = np.asarray(probs, dtype=float)
    outcomes = np.asarray(outcomes, dtype=float)
    if len(probs) == 0:
        return None
    return float(np.mean((probs - outcomes) ** 2))


def reliability_curve(probs, outcomes, n_bins=10) -> list:
    """
    Courbe de fiabilité : pour chaque tranche de proba prédite [0-10%, 10-20%, ...],
    la fréquence réelle observée. Un modèle calibré → points sur la diagonale.
    """
    probs = np.asarray(probs, dtype=float)
    outcomes = np.asarray(outcomes, dtype=float)
    bins = []
    for k in range(n_bins):
        lo, hi = k / n_bins, (k + 1) / n_bins
        if k == n_bins - 1:
            mask = (probs >= lo) & (probs <= hi)
        else:
            mask = (probs >= lo) & (probs < hi)
        cnt = int(mask.sum())
        bins.append({
            "bin_low": round(lo, 2),
            "bin_high": round(hi, 2),
            "predicted": round(float(probs[mask].mean()), 4) if cnt else None,
            "observed": round(float(outcomes[mask].mean()), 4) if cnt else None,
            "count": cnt,
        })
    return bins


class ProbabilityEngine:
    """
    Orchestrateur : charge/sauvegarde le modèle, entraîne depuis l'historique,
    produit la probabilité calibrée en live.
    """

    def __init__(self):
        self.model = self._load()

    def _path(self) -> str:
        return user_data_path(MODEL_FILE)

    def _load(self) -> LogisticModel:
        try:
            p = self._path()
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    return LogisticModel.from_dict(json.load(f))
        except Exception:
            pass
        return LogisticModel()

    def save(self, meta: dict = None):
        data = self.model.to_dict()
        if meta:
            data["meta"] = meta
        try:
            with open(self._path(), "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"[probability_engine] save error: {e}")

    def is_ready(self) -> bool:
        return self.model.trained

    def train_from_history(
        self, df: pd.DataFrame, symbol: str = "",
        sl_mult=1.5, tp_mult=3.0, horizon=24,
    ) -> dict:
        """
        Entraîne le modèle sur un DataFrame OHLCV historique.
        Retourne les métriques d'entraînement (in-sample) + calibration.
        """
        X, y = build_training_set(df, sl_mult, tp_mult, horizon)
        if len(X) < 30:
            return {"error": f"Pas assez de données ({len(X)} échantillons, min 30)"}

        # Split chronologique 80/20 pour valider la calibration hors échantillon
        split = int(len(X) * 0.8)
        X_tr, y_tr = X[:split], y[:split]
        X_te, y_te = X[split:], y[split:]

        model = LogisticModel()
        model.fit(X_tr, y_tr)
        self.model = model

        # Métriques test
        if len(X_te) >= 5:
            p_te = model.predict_proba(X_te)
            brier = brier_score(p_te, y_te)
            curve = reliability_curve(p_te, y_te)
            acc = float(((p_te >= 0.5).astype(float) == y_te).mean())
        else:
            brier, curve, acc = None, [], None

        meta = {
            "symbol": symbol,
            "n_total": int(len(X)),
            "n_train": int(len(X_tr)),
            "n_test": int(len(X_te)),
            "base_rate": round(float(y.mean()), 4),
            "test_brier": round(brier, 4) if brier is not None else None,
            "test_accuracy": round(acc, 4) if acc is not None else None,
            "sl_mult": sl_mult,
            "tp_mult": tp_mult,
            "horizon": horizon,
        }
        self.save(meta)

        return {
            "trained": True,
            "n_samples": int(len(X)),
            "n_train": int(len(X_tr)),
            "n_test": int(len(X_te)),
            "base_rate": meta["base_rate"],
            "test_brier": meta["test_brier"],
            "test_accuracy": meta["test_accuracy"],
            "reliability_curve": curve,
            "feature_importance": model.feature_importance(),
        }

    def predict(self, df: pd.DataFrame) -> dict:
        """
        Probabilité calibrée pour le dernier candle d'un DataFrame OHLCV.
        df peut être brut (indicateurs recalculés si absents).
        """
        if not self.model.trained:
            return {"available": False}
        if "rsi" not in df.columns or "atr" not in df.columns:
            df = compute_all_indicators(df.copy())
        feats = _feature_frame(df)
        x = feats.iloc[-1].values
        prob = self.model.predict_proba(x)
        return {
            "available": True,
            "prob_up": round(prob, 4),
            "prob_down": round(1 - prob, 4),
            "n_samples": self.model.n_samples,
        }

    def status(self) -> dict:
        m = self.model
        meta = {}
        try:
            p = self._path()
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    meta = json.load(f).get("meta", {})
        except Exception:
            pass
        return {
            "trained": m.trained,
            "n_samples": m.n_samples,
            "feature_importance": m.feature_importance() if m.trained else [],
            "meta": meta,
        }
