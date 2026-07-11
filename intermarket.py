"""
Analyse inter-marches - IA n7 des docs APEX.

Etudie les relations entre la crypto (BTC) et les grands marches macro :
dollar (DXY), or, S&P 500, VIX. Deux sorties :
  1. Correlations (structure) : BTC vs chaque actif sur 90j de rendements quotidiens
  2. Biais macro (actionnable) : l'environnement macro est-il porteur (tailwind) ou
     contraire (headwind) pour la crypto en ce moment.

Utilise yfinance (deja installe, aucune nouvelle dependance). Donnees quotidiennes
alignees sur les dates communes (les marches traditionnels ferment le weekend).
Cache 2h (donnees daily), echec silencieux : available=False sans exception.
"""

import time
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

_CACHE = {"ts": 0.0, "data": None}
_TTL = 7200  # 2h

# nom lisible -> ticker yfinance
_ASSETS = {
    "Dollar (DXY)": "DX-Y.NYB",
    "Or":           "GC=F",
    "S&P 500":      "^GSPC",
    "VIX":          "^VIX",
}


def get_intermarket_analysis() -> dict:
    now = time.time()
    if _CACHE["data"] is not None and (now - _CACHE["ts"]) < _TTL:
        return _CACHE["data"]
    try:
        data = _compute()
        if data.get("available"):
            _CACHE["ts"] = now
            _CACHE["data"] = data
        return data
    except Exception:
        return _CACHE["data"] if _CACHE["data"] is not None else {"available": False}


def _close_series(ticker: str) -> pd.Series:
    import yfinance as yf
    df = yf.Ticker(ticker).history(period="90d", interval="1d")
    if df is None or df.empty:
        return pd.Series(dtype=float)
    s = df["Close"].copy()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    return s


def _compute() -> dict:
    btc = _close_series("BTC-USD")
    if len(btc) < 30:
        return {"available": False}

    correlations = []
    assets_info = {}
    series_map = {}
    for name, tk in _ASSETS.items():
        try:
            s = _close_series(tk)
        except Exception:
            s = pd.Series(dtype=float)
        if len(s) < 30:
            continue
        series_map[name] = s
        # infos prix + variation 5j
        last = float(s.iloc[-1])
        chg5 = float(s.iloc[-1] / s.iloc[-6] - 1) * 100 if len(s) > 6 else 0.0
        assets_info[name] = {"price": round(last, 2), "chg_5d": round(chg5, 2)}

        # correlation des rendements quotidiens sur dates communes
        joined = pd.concat([btc.rename("btc"), s.rename("a")], axis=1).dropna()
        if len(joined) < 20:
            continue
        rets = joined.pct_change().dropna()
        corr = float(rets["btc"].corr(rets["a"]))
        correlations.append({
            "asset": name,
            "corr": round(corr, 2),
            "interpretation": _corr_label(name, corr),
        })

    # ── Biais macro pour la crypto ──
    score = 0
    drivers = []

    sp = series_map.get("S&P 500")
    if sp is not None and len(sp) > 20:
        sp_mom = sp.iloc[-1] / sp.iloc[-20] - 1
        if sp_mom > 0.01:
            score += 1; drivers.append("S&P haussier : appetit pour le risque (tailwind)")
        elif sp_mom < -0.01:
            score -= 1; drivers.append("S&P baissier : aversion au risque (headwind)")

    dxy = series_map.get("Dollar (DXY)")
    if dxy is not None and len(dxy) > 20:
        dxy_mom = dxy.iloc[-1] / dxy.iloc[-20] - 1
        if dxy_mom > 0.01:
            score -= 1; drivers.append("Dollar en hausse : pression sur la crypto (headwind)")
        elif dxy_mom < -0.01:
            score += 1; drivers.append("Dollar en baisse : favorable a la crypto (tailwind)")

    vix = series_map.get("VIX")
    if vix is not None and len(vix):
        vix_level = float(vix.iloc[-1])
        if vix_level > 25:
            score -= 1; drivers.append(f"VIX eleve ({vix_level:.0f}) : stress de marche (headwind)")
        elif vix_level < 15:
            score += 1; drivers.append(f"VIX bas ({vix_level:.0f}) : marche calme (tailwind)")

    if score >= 2:
        bias, bias_label = "FAVORABLE", "Environnement macro porteur pour la crypto"
    elif score <= -2:
        bias, bias_label = "UNFAVORABLE", "Environnement macro contraire a la crypto"
    else:
        bias, bias_label = "NEUTRAL", "Environnement macro neutre"

    if not correlations:
        return {"available": False}

    return {
        "available": True,
        "correlations": correlations,
        "macro_bias": bias,
        "macro_bias_label": bias_label,
        "macro_bias_score": score,
        "drivers": drivers,
        "assets": assets_info,
        "n_days": int(len(btc)),
    }


def _corr_label(name: str, corr: float) -> str:
    strength = "forte" if abs(corr) >= 0.5 else "modérée" if abs(corr) >= 0.25 else "faible"
    sign = "positive" if corr >= 0 else "négative"
    return f"Corrélation {sign} {strength}"
