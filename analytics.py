"""
Module d'analyse quantitative avancée - niveau institutionnel.

Contient :
- Optimisation Mean-Variance (frontière efficiente de Markowitz)
- Portefeuilles optimaux : Max Sharpe, Min Variance, Risk Parity, Equal Weight
- Matrice de corrélation + ratio de diversification
- Stress tests historiques (COVID, 2008, 2022, krach banque 2023)
- Métriques glissantes (Rolling Sharpe, Rolling Vol)
- Détection du régime de marché (Bull/Bear/Crisis/Neutral)
- Risque de queue : skewness, kurtosis, VaR, CVaR
- Kelly Criterion par actif
- Attribution de performance
"""

import numpy as np
import pandas as pd
from datetime import datetime
from market_data import get_historical_data
from config import PORTFOLIO

try:
    from scipy.optimize import minimize
    HAS_SCIPY = True
except Exception:
    HAS_SCIPY = False


# ─────────────────────────────────────────────────────────
#  ENTRYPOINT
# ─────────────────────────────────────────────────────────

def full_analytics(years: int = 5, custom_weights: dict = None) -> dict:
    """Calcule toutes les métriques d'analyse avancée du portefeuille."""
    prices = get_historical_data(years)
    if prices.empty:
        return {"error": "Pas de données historiques"}

    weights_cfg = custom_weights or {t: cfg["target_pct"] / 100 for t, cfg in PORTFOLIO.items()}
    tickers = [t for t in weights_cfg if t in prices.columns]
    if len(tickers) < 2:
        return {"error": "Au moins 2 ETFs nécessaires pour l'analyse"}

    prices = prices[tickers].dropna()
    returns = prices.pct_change().dropna()

    # Poids actuels normalisés
    cw = np.array([weights_cfg[t] for t in tickers])
    cw = cw / cw.sum()

    # μ, Σ annualisés
    mu = returns.mean().values * 252
    cov = returns.cov().values * 252
    sigma_diag = np.sqrt(np.diag(cov))

    return {
        "meta": {
            "tickers": tickers,
            "names": [PORTFOLIO.get(t, {}).get("name", t) for t in tickers],
            "categories": [PORTFOLIO.get(t, {}).get("category", "") for t in tickers],
            "start_date": str(prices.index[0].date()),
            "end_date": str(prices.index[-1].date()),
            "n_days": int(len(prices)),
        },
        "expected_returns_pct": (mu * 100).round(2).tolist(),
        "volatilities_pct": (sigma_diag * 100).round(2).tolist(),
        "current_weights_pct": (cw * 100).round(2).tolist(),
        "correlation": _corr_matrix(returns, tickers),
        "diversification_ratio": _diversification_ratio(cw, sigma_diag, cov),
        "efficient_frontier": _efficient_frontier(mu, cov, tickers, cw),
        "stress_tests": _stress_tests(prices, cw, tickers),
        "rolling": _rolling_metrics(returns, cw),
        "regime": _detect_regime(prices),
        "tail_risk": _tail_risk(returns, cw),
        "kelly": _kelly_per_asset(returns, tickers),
        "portfolio_stats": _portfolio_stats(returns, cw, tickers),
    }


# ─────────────────────────────────────────────────────────
#  OPTIMISATION - Mean-Variance (Markowitz)
# ─────────────────────────────────────────────────────────

def _port_ret(w, mu): return float(np.dot(w, mu))
def _port_vol(w, cov):
    v = float(w @ cov @ w)
    return float(np.sqrt(max(v, 0)))
def _port_sharpe(w, mu, cov, rf=0.02):
    v = _port_vol(w, cov)
    return (_port_ret(w, mu) - rf) / v if v > 0 else 0.0


def _efficient_frontier(mu: np.ndarray, cov: np.ndarray,
                        tickers: list, current_w: np.ndarray) -> dict:
    """Calcule la frontière efficiente et 5 portefeuilles remarquables."""
    n = len(tickers)
    bounds = [(0.0, 1.0)] * n
    cons_sum = {"type": "eq", "fun": lambda w: np.sum(w) - 1.0}
    w0 = np.ones(n) / n

    def _point(w, label, color):
        r = _port_ret(w, mu)
        v = _port_vol(w, cov)
        return {
            "label": label,
            "color": color,
            "return_pct": round(r * 100, 2),
            "volatility_pct": round(v * 100, 2),
            "sharpe": round(_port_sharpe(w, mu, cov), 2),
            "weights": [
                {"ticker": t, "name": PORTFOLIO.get(t, {}).get("name", t),
                 "weight_pct": round(float(wi * 100), 2)}
                for t, wi in zip(tickers, w)
            ],
        }

    # Equal weight : toujours calculable
    ew = np.ones(n) / n

    if not HAS_SCIPY:
        return {
            "error": "scipy indisponible - optimisation désactivée",
            "current": _point(current_w, "Portefeuille actuel", "#2997ff"),
            "equal_weight": _point(ew, "Equal Weight", "#ff9f0a"),
        }

    # Max Sharpe
    res_ms = minimize(lambda w: -_port_sharpe(w, mu, cov),
                      w0, bounds=bounds, constraints=[cons_sum], method="SLSQP")
    w_ms = res_ms.x if res_ms.success else w0

    # Min variance
    res_mv = minimize(lambda w: _port_vol(w, cov),
                      w0, bounds=bounds, constraints=[cons_sum], method="SLSQP")
    w_mv = res_mv.x if res_mv.success else w0

    # Risk Parity (Equal Risk Contribution)
    def _rp_obj(w):
        v = _port_vol(w, cov)
        if v <= 0:
            return 1e6
        marg = cov @ w / v
        rc = w * marg
        return float(np.sum((rc - v / n) ** 2))
    res_rp = minimize(_rp_obj, w0, bounds=bounds, constraints=[cons_sum], method="SLSQP")
    w_rp = res_rp.x if res_rp.success else ew

    # Frontière : minimiser variance sous contrainte rendement cible
    target_returns = np.linspace(float(mu.min()), float(mu.max()), 30)
    frontier = []
    for tr in target_returns:
        cons = [cons_sum, {"type": "eq", "fun": lambda w, tr=tr: _port_ret(w, mu) - tr}]
        res = minimize(lambda w: _port_vol(w, cov), w0,
                       bounds=bounds, constraints=cons, method="SLSQP")
        if res.success:
            frontier.append({
                "volatility_pct": round(_port_vol(res.x, cov) * 100, 3),
                "return_pct": round(tr * 100, 3),
            })

    # Nuage de portefeuilles aléatoires pour contexte
    rng = np.random.default_rng(42)
    cloud = []
    for _ in range(800):
        w = rng.dirichlet(np.ones(n))
        cloud.append({
            "volatility_pct": round(_port_vol(w, cov) * 100, 3),
            "return_pct": round(_port_ret(w, mu) * 100, 3),
            "sharpe": round(_port_sharpe(w, mu, cov), 2),
        })

    return {
        "frontier": frontier,
        "random_cloud": cloud,
        "current":       _point(current_w, "Portefeuille actuel", "#2997ff"),
        "equal_weight":  _point(ew,         "Equal Weight",        "#ff9f0a"),
        "min_variance":  _point(w_mv,       "Min Variance",        "#30d158"),
        "max_sharpe":    _point(w_ms,       "Max Sharpe",          "#bf5af2"),
        "risk_parity":   _point(w_rp,       "Risk Parity",         "#64d2ff"),
    }


# ─────────────────────────────────────────────────────────
#  CORRÉLATION & DIVERSIFICATION
# ─────────────────────────────────────────────────────────

def _corr_matrix(returns: pd.DataFrame, tickers: list) -> dict:
    corr = returns.corr()
    # Moyenne des corrélations hors diagonale
    n = len(corr)
    off_diag = corr.values[np.triu_indices(n, k=1)]
    avg_corr = float(np.mean(off_diag)) if len(off_diag) > 0 else 0.0
    return {
        "tickers": tickers,
        "names": [PORTFOLIO.get(t, {}).get("name", t) for t in tickers],
        "matrix": [[round(float(v), 3) for v in row] for row in corr.values],
        "average": round(avg_corr, 3),
    }


def _diversification_ratio(weights: np.ndarray, sigmas: np.ndarray,
                           cov: np.ndarray) -> dict:
    """DR = Σ(w*σ) / σ_port. Plus DR > 1, meilleure est la diversification."""
    weighted_avg_vol = float(np.dot(weights, sigmas))
    port_vol = float(np.sqrt(weights @ cov @ weights))
    dr = weighted_avg_vol / port_vol if port_vol > 0 else 1.0
    return {
        "value": round(dr, 3),
        "weighted_avg_vol_pct": round(weighted_avg_vol * 100, 2),
        "portfolio_vol_pct": round(port_vol * 100, 2),
        "interpretation": (
            "Excellente" if dr >= 1.5 else
            "Bonne" if dr >= 1.2 else
            "Moyenne" if dr >= 1.05 else
            "Faible"
        ),
    }


# ─────────────────────────────────────────────────────────
#  STRESS TESTS - Rejouer des crises historiques
# ─────────────────────────────────────────────────────────

def _stress_tests(prices: pd.DataFrame, weights: np.ndarray,
                  tickers: list) -> list:
    scenarios = [
        {"name": "COVID Crash",           "desc": "Chute pandémie",       "start": "2020-02-19", "end": "2020-03-23"},
        {"name": "Reprise post-COVID",    "desc": "Rebond 2020-2021",     "start": "2020-03-24", "end": "2021-12-31"},
        {"name": "Hausse des taux 2022",  "desc": "Fed hawkish",          "start": "2022-01-03", "end": "2022-10-12"},
        {"name": "Crise bancaire 2023",   "desc": "SVB / Credit Suisse",  "start": "2023-03-08", "end": "2023-03-20"},
        {"name": "Krach 2008",            "desc": "Lehman Brothers",      "start": "2008-09-15", "end": "2009-03-09"},
        {"name": "Correction août 2024",  "desc": "Yen carry trade",      "start": "2024-07-31", "end": "2024-08-05"},
    ]
    out = []
    for sc in scenarios:
        try:
            w = prices.loc[sc["start"]:sc["end"]]
            if len(w) < 2:
                continue
            rets = (w.iloc[-1] / w.iloc[0] - 1).values
            port_ret = float(np.dot(rets, weights)) * 100
            # Max drawdown sur la période
            port_series = (w * weights).sum(axis=1) / (w.iloc[0] * weights).sum()
            dd = ((port_series / port_series.cummax()) - 1).min() * 100
            out.append({
                "name": sc["name"],
                "description": sc["desc"],
                "period": f"{sc['start']} → {sc['end']}",
                "portfolio_return_pct": round(port_ret, 2),
                "portfolio_max_dd_pct": round(float(dd), 2),
                "days": int(len(w)),
                "etf_returns": [
                    {"ticker": t,
                     "name": PORTFOLIO.get(t, {}).get("name", t),
                     "return_pct": round(float(r * 100), 2)}
                    for t, r in zip(tickers, rets)
                ],
            })
        except Exception:
            continue
    return out


# ─────────────────────────────────────────────────────────
#  ROLLING METRICS
# ─────────────────────────────────────────────────────────

def _rolling_metrics(returns: pd.DataFrame, weights: np.ndarray,
                     window: int = 63) -> dict:
    """Sharpe/vol glissants (fenêtre par défaut : ~3 mois de trading)."""
    port_ret = returns @ weights
    ann_factor = np.sqrt(252)

    roll_vol = port_ret.rolling(window).std() * ann_factor * 100
    roll_mu = port_ret.rolling(window).mean() * 252
    roll_sharpe = (roll_mu - 0.02) / (port_ret.rolling(window).std() * ann_factor)

    # Drawdown roulant : distance au pic sur fenêtre rolling
    port_curve = (1 + port_ret).cumprod()
    roll_dd = ((port_curve / port_curve.cummax()) - 1) * 100

    def _clean(s):
        return [None if pd.isna(v) else round(float(v), 2) for v in s]

    return {
        "dates": [str(d.date()) for d in port_ret.index],
        "rolling_vol_pct": _clean(roll_vol),
        "rolling_sharpe": _clean(roll_sharpe),
        "rolling_drawdown_pct": _clean(roll_dd),
        "window_days": window,
    }


# ─────────────────────────────────────────────────────────
#  RÉGIME DE MARCHÉ
# ─────────────────────────────────────────────────────────

def _detect_regime(prices: pd.DataFrame) -> dict:
    """Détection du régime via SPY : SMA200, drawdown, momentum 30j."""
    try:
        import yfinance as yf
        spy = yf.download("SPY", start=prices.index[0], end=prices.index[-1],
                          progress=False)["Close"]
        if isinstance(spy, pd.DataFrame):
            spy = spy.iloc[:, 0]
        spy = spy.dropna()
        if len(spy) < 200:
            return {"error": "Historique insuffisant"}

        price = float(spy.iloc[-1])
        sma200 = float(spy.rolling(200).mean().iloc[-1])
        peak = float(spy.expanding().max().iloc[-1])
        dd = (price / peak - 1) * 100
        r30 = (price / float(spy.iloc[-30]) - 1) * 100 if len(spy) >= 30 else 0
        vol30 = float(spy.pct_change().iloc[-30:].std()) * np.sqrt(252) * 100

        vs_sma = (price / sma200 - 1) * 100

        # Logique de régime
        if dd < -20:
            regime, label, color = "crisis", "Bear market / Crise", "red"
        elif vs_sma < -5 and r30 < -3:
            regime, label, color = "bear", "Correction / Bearish", "red"
        elif vs_sma > 3 and r30 > 0 and vol30 < 25:
            regime, label, color = "bull", "Marché haussier", "green"
        elif vol30 > 30:
            regime, label, color = "volatile", "Forte volatilité", "orange"
        else:
            regime, label, color = "neutral", "Neutre / Consolidation", "blue"

        return {
            "regime": regime,
            "label": label,
            "color": color,
            "spy_price": round(price, 2),
            "sma200": round(sma200, 2),
            "vs_sma_pct": round(vs_sma, 2),
            "drawdown_pct": round(dd, 2),
            "return_30d_pct": round(float(r30), 2),
            "vol_30d_pct": round(vol30, 2),
        }
    except Exception as e:
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────
#  TAIL RISK
# ─────────────────────────────────────────────────────────

def _tail_risk(returns: pd.DataFrame, weights: np.ndarray) -> dict:
    port_ret = returns @ weights
    if len(port_ret) < 20:
        return {}

    r = port_ret.dropna()
    mean = float(r.mean())
    std = float(r.std())
    # Moments 3 et 4
    skew = float(((r - mean) ** 3).mean() / (std ** 3)) if std > 0 else 0
    kurt = float(((r - mean) ** 4).mean() / (std ** 4)) if std > 0 else 0
    excess_kurt = kurt - 3  # excès vs distribution normale

    var95 = float(np.percentile(r, 5)) * 100
    var99 = float(np.percentile(r, 1)) * 100
    cvar95 = float(r[r <= np.percentile(r, 5)].mean()) * 100
    cvar99 = float(r[r <= np.percentile(r, 1)].mean()) * 100

    # Plus gros gain/perte quotidiens
    best_day = float(r.max()) * 100
    worst_day = float(r.min()) * 100

    # Interprétation skew
    if skew < -0.5:
        skew_label = "Risque asymétrique (queues négatives épaisses)"
    elif skew > 0.5:
        skew_label = "Biais positif (gains extrêmes plus probables)"
    else:
        skew_label = "Distribution équilibrée"

    return {
        "skewness": round(skew, 3),
        "skewness_label": skew_label,
        "kurtosis": round(kurt, 3),
        "excess_kurtosis": round(excess_kurt, 3),
        "fat_tails": excess_kurt > 1,  # distribution à queues épaisses
        "var_95_pct": round(var95, 2),
        "var_99_pct": round(var99, 2),
        "cvar_95_pct": round(cvar95, 2),
        "cvar_99_pct": round(cvar99, 2),
        "best_day_pct": round(best_day, 2),
        "worst_day_pct": round(worst_day, 2),
    }


# ─────────────────────────────────────────────────────────
#  KELLY CRITERION par ETF
# ─────────────────────────────────────────────────────────

def _kelly_per_asset(returns: pd.DataFrame, tickers: list) -> list:
    """Fraction de Kelly pour chaque ETF : f = μ/σ² (approx pour actifs)."""
    results = []
    for t in tickers:
        r = returns[t].dropna()
        mu = float(r.mean()) * 252
        var = float(r.var()) * 252
        kelly = mu / var if var > 0 else 0
        # Clipping : Kelly pur est trop agressif, on affiche 1/4 Kelly (institutionnel)
        kelly_full = max(-1.0, min(kelly, 2.0))
        kelly_quarter = max(0.0, kelly_full * 0.25)
        results.append({
            "ticker": t,
            "name": PORTFOLIO.get(t, {}).get("name", t),
            "mu_annual_pct": round(mu * 100, 2),
            "sigma_annual_pct": round(float(np.sqrt(var)) * 100, 2),
            "kelly_full_pct": round(kelly_full * 100, 2),
            "kelly_quarter_pct": round(kelly_quarter * 100, 2),
        })
    # Normalisation Kelly 1/4 à 100% pour proposer une allocation
    total = sum(max(r["kelly_quarter_pct"], 0) for r in results)
    for r in results:
        r["normalized_allocation_pct"] = (
            round(max(r["kelly_quarter_pct"], 0) / total * 100, 2) if total > 0 else 0
        )
    return sorted(results, key=lambda x: x["kelly_full_pct"], reverse=True)


# ─────────────────────────────────────────────────────────
#  STATS PORTEFEUILLE COMPLET
# ─────────────────────────────────────────────────────────

def _portfolio_stats(returns: pd.DataFrame, weights: np.ndarray,
                     tickers: list) -> dict:
    """Stats globales du portefeuille actuel."""
    port_ret = returns @ weights
    if len(port_ret) < 2:
        return {}

    ann_ret = float(port_ret.mean()) * 252
    ann_vol = float(port_ret.std()) * np.sqrt(252)
    sharpe = (ann_ret - 0.02) / ann_vol if ann_vol > 0 else 0

    downside = port_ret[port_ret < 0]
    downside_vol = float(downside.std()) * np.sqrt(252) if len(downside) > 0 else 0
    sortino = (ann_ret - 0.02) / downside_vol if downside_vol > 0 else 0

    curve = (1 + port_ret).cumprod()
    max_dd = float(((curve / curve.cummax()) - 1).min()) * 100
    calmar = (ann_ret / abs(max_dd / 100)) if max_dd < 0 else 0

    # Contribution au risque par actif : RC_i = w_i * (Σw)_i / σ_p
    cov_values = returns.cov().values * 252
    w = np.array(weights)
    port_vol_sq = float(w @ cov_values @ w)
    port_vol = np.sqrt(max(port_vol_sq, 1e-12))
    marginal = cov_values @ w / port_vol
    rc = w * marginal
    rc_pct = (rc / port_vol * 100).tolist()

    return {
        "annual_return_pct": round(ann_ret * 100, 2),
        "annual_volatility_pct": round(ann_vol * 100, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "calmar": round(calmar, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "risk_contribution": [
            {"ticker": t, "name": PORTFOLIO.get(t, {}).get("name", t),
             "weight_pct": round(float(weights[i] * 100), 2),
             "risk_contribution_pct": round(float(rc_pct[i]), 2)}
            for i, t in enumerate(tickers)
        ],
    }
