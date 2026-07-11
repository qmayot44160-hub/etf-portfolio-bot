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

    def is_compatible(self) -> bool:
        """
        Le modèle chargé correspond-il à la liste de features courante ?
        Empêche un modèle sauvé (ancien vecteur) d'être utilisé après un changement
        de FEATURE_NAMES : sinon la prédiction lèverait une erreur de shape
        silencieusement avalée. Incompatible → traité comme non entraîné → réentraîné.
        """
        return (
            self.weights is not None
            and list(self.feature_names) == list(FEATURE_NAMES)
            and len(self.weights) == len(FEATURE_NAMES)
        )


class GaussianNB:
    """
    Naive Bayes gaussien binaire (pur numpy). Modèle GÉNÉRATIF : il modélise la
    distribution de chaque feature par classe, là où la régression logistique est
    DISCRIMINATIVE. Deux familles différentes → erreurs décorrélées → la moyenne
    de leurs probabilités est plus robuste (ensemble learning des docs APEX,
    "qu'une seule IA ne décide jamais seule"). Même interface que LogisticModel.
    """

    def __init__(self):
        self.theta = None    # moyennes par classe (2, f)
        self.var = None      # variances par classe (2, f)
        self.priors = None   # (2,)
        self.trained = False
        self.n_samples = 0
        self.feature_names = list(FEATURE_NAMES)

    def fit(self, X, y):
        if len(X) < 30:
            raise ValueError(f"Pas assez de données ({len(X)} < 30)")
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        f = X.shape[1]
        self.theta = np.zeros((2, f))
        self.var = np.ones((2, f))
        self.priors = np.full(2, 1e-9)
        for i, c in enumerate((0.0, 1.0)):
            Xc = X[y == c]
            if len(Xc) == 0:
                continue
            self.theta[i] = Xc.mean(axis=0)
            self.var[i] = Xc.var(axis=0) + 1e-6   # epsilon : évite la division par zéro
            self.priors[i] = len(Xc) / len(X)
        self.trained = True
        self.n_samples = int(len(X))
        return self

    def predict_proba(self, x):
        """x : vecteur (f,) OU matrice (n, f). Retourne proba(s) de la classe 1."""
        if not self.trained:
            raise RuntimeError("Modèle non entraîné")
        x = np.asarray(x, dtype=float)
        single = x.ndim == 1
        if single:
            x = x.reshape(1, -1)
        ll = np.zeros((x.shape[0], 2))
        for i in range(2):
            log_prior = np.log(self.priors[i] + 1e-12)
            log_gauss = -0.5 * np.sum(
                np.log(2 * np.pi * self.var[i]) + (x - self.theta[i]) ** 2 / self.var[i],
                axis=1,
            )
            ll[:, i] = log_prior + log_gauss
        # softmax numériquement stable → P(classe = 1)
        m = ll.max(axis=1, keepdims=True)
        e = np.exp(ll - m)
        p1 = e[:, 1] / e.sum(axis=1)
        return float(p1[0]) if single else p1

    def to_dict(self) -> dict:
        return {
            "theta": self.theta.tolist() if self.theta is not None else None,
            "var": self.var.tolist() if self.var is not None else None,
            "priors": self.priors.tolist() if self.priors is not None else None,
            "trained": self.trained,
            "n_samples": self.n_samples,
            "feature_names": self.feature_names,
        }

    @classmethod
    def from_dict(cls, d: dict):
        m = cls()
        if not d or not d.get("trained"):
            return m
        m.theta = np.array(d["theta"], dtype=float)
        m.var = np.array(d["var"], dtype=float)
        m.priors = np.array(d["priors"], dtype=float)
        m.trained = True
        m.n_samples = int(d.get("n_samples", 0))
        m.feature_names = d.get("feature_names", list(FEATURE_NAMES))
        return m

    def is_compatible(self) -> bool:
        return (
            self.theta is not None
            and list(self.feature_names) == list(FEATURE_NAMES)
            and self.theta.shape[1] == len(FEATURE_NAMES)
        )


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
        self.model = LogisticModel()   # discriminatif
        self.nb = GaussianNB()         # génératif
        self.ens_weights = [0.5, 0.5]  # [logistic, nb], pondérés par la calibration
        self._load()

    def _path(self) -> str:
        return user_data_path(MODEL_FILE)

    def _load(self):
        try:
            p = self._path()
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "logistic" in data:   # nouveau format ensemble
                    self.model = LogisticModel.from_dict(data["logistic"])
                    self.nb = GaussianNB.from_dict(data.get("nb", {}))
                    self.ens_weights = data.get("ensemble_weights", [0.5, 0.5])
                else:                    # ancien format (logistique seule)
                    # nb absent → is_ready False → réentraînement de l'ensemble
                    self.model = LogisticModel.from_dict(data)
                    self.nb = GaussianNB()
        except Exception:
            self.model = LogisticModel()
            self.nb = GaussianNB()

    def save(self, meta: dict = None):
        data = {
            "logistic": self.model.to_dict(),
            "nb": self.nb.to_dict(),
            "ensemble_weights": self.ens_weights,
        }
        if meta:
            data["meta"] = meta
        try:
            with open(self._path(), "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"[probability_engine] save error: {e}")

    def is_ready(self) -> bool:
        # Ensemble prêt = les deux modèles entraînés ET compatibles avec les features
        return (
            self.model.trained and self.model.is_compatible()
            and self.nb.trained and self.nb.is_compatible()
        )

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
        nb = GaussianNB()
        nb.fit(X_tr, y_tr)
        self.model = model
        self.nb = nb

        # Sélection de la pondération d'ensemble par validation sur le test :
        # on compare LR seul, NB seul, et le mélange pondéré (inverse-Brier), puis
        # on garde la config la MIEUX calibrée. Garantit que l'ensemble ne fait
        # jamais pire que le meilleur modèle seul (un membre faible ne dégrade pas).
        if len(X_te) >= 5:
            p_lr = model.predict_proba(X_te)
            p_nb = nb.predict_proba(X_te)
            brier_lr = brier_score(p_lr, y_te)
            brier_nb = brier_score(p_nb, y_te)
            inv_lr = 1.0 / max(brier_lr, 0.01)
            inv_nb = 1.0 / max(brier_nb, 0.01)
            w_blend = inv_lr / (inv_lr + inv_nb)
            p_blend = w_blend * p_lr + (1 - w_blend) * p_nb
            options = [
                ([1.0, 0.0], brier_lr, p_lr),
                ([0.0, 1.0], brier_nb, p_nb),
                ([round(w_blend, 4), round(1 - w_blend, 4)], brier_score(p_blend, y_te), p_blend),
            ]
            best_w, brier, p_te = min(options, key=lambda o: o[1])
            self.ens_weights = best_w
            curve = reliability_curve(p_te, y_te)
            acc = float(((p_te >= 0.5).astype(float) == y_te).mean())
        else:
            brier, curve, acc = None, [], None
            brier_lr = brier_nb = None
            self.ens_weights = [0.5, 0.5]

        meta = {
            "symbol": symbol,
            "n_total": int(len(X)),
            "n_train": int(len(X_tr)),
            "n_test": int(len(X_te)),
            "base_rate": round(float(y.mean()), 4),
            "test_brier": round(brier, 4) if brier is not None else None,
            "test_brier_lr": round(brier_lr, 4) if brier_lr is not None else None,
            "test_brier_nb": round(brier_nb, 4) if brier_nb is not None else None,
            "test_accuracy": round(acc, 4) if acc is not None else None,
            "ensemble": True,
            "models": ["logistic", "gaussian_nb"],
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
            "test_brier_lr": meta["test_brier_lr"],
            "test_brier_nb": meta["test_brier_nb"],
            "test_accuracy": meta["test_accuracy"],
            "reliability_curve": curve,
            "feature_importance": model.feature_importance(),
        }

    def predict(self, df: pd.DataFrame) -> dict:
        """
        Probabilité calibrée pour le dernier candle d'un DataFrame OHLCV.
        df peut être brut (indicateurs recalculés si absents).
        """
        if not self.is_ready():
            return {"available": False}
        if "rsi" not in df.columns or "atr" not in df.columns:
            df = compute_all_indicators(df.copy())
        feats = _feature_frame(df)
        x = feats.iloc[-1].values
        p_lr = self.model.predict_proba(x)
        p_nb = self.nb.predict_proba(x)
        w_lr, w_nb = self.ens_weights      # pondéré par la calibration
        prob = w_lr * p_lr + w_nb * p_nb
        return {
            "available": True,
            "prob_up": round(prob, 4),
            "prob_down": round(1 - prob, 4),
            "prob_lr": round(p_lr, 4),
            "prob_nb": round(p_nb, 4),
            "ens_weights": self.ens_weights,
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
            "trained": self.is_ready(),
            "n_samples": m.n_samples,
            "feature_importance": m.feature_importance() if m.trained else [],
            "meta": meta,
        }
