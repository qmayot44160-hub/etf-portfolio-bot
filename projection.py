"""
Module de projection future — simulation Monte Carlo des gains futurs.

Au lieu de regarder le passé (backtest), on projette dans le futur :
- Calibration μ/σ depuis l'historique réel du portefeuille pondéré
- Monte Carlo : N simulations (Geometric Brownian Motion) avec DCA mensuel
- Bandes de confiance (P5 / P25 / P50 / P75 / P95)
- Scénarios déterministes : Pessimiste (-1σ) / Médian / Optimiste (+1σ)
- Probabilité d'atteindre un objectif
- Probabilité de perte nominale
- Valeur réelle ajustée inflation
"""

import numpy as np
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta
from market_data import get_historical_data
from config import PORTFOLIO, INITIAL_CAPITAL, DCA_MONTHLY


def run_projection(
    capital: float = None,
    dca: float = None,
    years: int = 10,
    n_simulations: int = 2000,
    inflation_pct: float = 2.0,
    target_amount: float = None,
    history_years: int = 5,
    custom_weights: dict = None,
) -> dict:
    """
    Projection Monte Carlo des gains futurs avec DCA mensuel.
    Calibration automatique sur l'historique réel du portefeuille.
    """
    if capital is None:
        capital = INITIAL_CAPITAL
    if dca is None:
        dca = DCA_MONTHLY

    # ── 1. Calibration depuis l'historique ──────────────
    prices = get_historical_data(history_years)
    if prices.empty:
        return {"error": "Pas de données historiques pour calibrer la projection"}

    weights = custom_weights or {t: cfg["target_pct"] / 100 for t, cfg in PORTFOLIO.items()}
    tickers = [t for t in weights if t in prices.columns]
    if not tickers:
        return {"error": "Aucun ticker dans les données"}

    total_w = sum(weights[t] for t in tickers)
    w = {t: weights[t] / total_w for t in tickers}

    daily_ret = prices[tickers].pct_change().dropna()
    portfolio_ret = sum(daily_ret[t] * w[t] for t in tickers)

    mu_daily = float(portfolio_ret.mean())
    sigma_daily = float(portfolio_ret.std())

    # Annualisation
    mu_annual = (1 + mu_daily) ** 252 - 1
    sigma_annual = sigma_daily * np.sqrt(252)

    # ── 2. Monte Carlo (mensuel, GBM discrétisé) ────────
    n_months = years * 12
    mu_m = (1 + mu_annual) ** (1 / 12) - 1
    sigma_m = sigma_annual / np.sqrt(12)

    rng = np.random.default_rng(42)
    paths = np.zeros((n_simulations, n_months + 1))
    paths[:, 0] = capital

    # Drift corrigé GBM : exp((μ - σ²/2) + σ·Z)
    drift = np.log(1 + mu_m) - 0.5 * sigma_m ** 2

    for t in range(1, n_months + 1):
        z = rng.standard_normal(n_simulations)
        ret = np.exp(drift + sigma_m * z)
        paths[:, t] = paths[:, t - 1] * ret + dca

    total_invested = capital + dca * n_months

    # ── 3. Percentiles par pas de temps ─────────────────
    def _pct(a, p): return np.percentile(a, p, axis=0)
    p5  = _pct(paths, 5)
    p25 = _pct(paths, 25)
    p50 = _pct(paths, 50)
    p75 = _pct(paths, 75)
    p95 = _pct(paths, 95)

    final_vals = paths[:, -1]

    # ── 4. Probabilités ─────────────────────────────────
    prob_loss = float((final_vals < total_invested).mean() * 100)
    prob_target = None
    if target_amount:
        prob_target = float((final_vals >= target_amount).mean() * 100)

    # ── 5. Valeur réelle (inflation) ────────────────────
    inflation_factor = (1 + inflation_pct / 100) ** years
    real_p50 = float(p50[-1]) / inflation_factor

    # ── 6. Scénarios déterministes ──────────────────────
    scenarios = _compute_scenarios(capital, dca, years, {
        "bear": max(mu_annual - sigma_annual, -0.5),
        "base": mu_annual,
        "bull": mu_annual + sigma_annual,
    })

    # ── 7. Échantillon de trajectoires pour affichage ───
    sample_idx = rng.choice(n_simulations, min(60, n_simulations), replace=False)
    sample_paths = paths[sample_idx]

    # ── 8. Dates mensuelles ─────────────────────────────
    today = datetime.now().replace(day=1)
    dates = [(today + relativedelta(months=i)).strftime("%Y-%m") for i in range(n_months + 1)]

    # ── 9. Table annuelle (médiane) ─────────────────────
    yearly = []
    for y in range(years + 1):
        idx = y * 12
        invested = capital + dca * idx
        yearly.append({
            "year": y,
            "invested": round(invested, 2),
            "p5": round(float(p5[idx]), 2),
            "p50": round(float(p50[idx]), 2),
            "p95": round(float(p95[idx]), 2),
            "gain_p50": round(float(p50[idx]) - invested, 2),
        })

    # ── 10. Contribution par ETF (projection individuelle) ─
    etf_projection = []
    for t in tickers:
        ret_t = daily_ret[t]
        mu_t = (1 + ret_t.mean()) ** 252 - 1
        sigma_t = ret_t.std() * np.sqrt(252)
        # Allocation individuelle du capital + DCA
        alloc_capital = capital * w[t]
        alloc_dca = dca * w[t]
        monthly_r = (1 + mu_t) ** (1 / 12) - 1
        val = alloc_capital
        for _ in range(n_months):
            val = val * (1 + monthly_r) + alloc_dca
        total_alloc = alloc_capital + alloc_dca * n_months
        etf_projection.append({
            "ticker": t,
            "name": PORTFOLIO.get(t, {}).get("name", t),
            "weight": round(w[t] * 100, 1),
            "mu_annual_pct": round(mu_t * 100, 2),
            "sigma_annual_pct": round(sigma_t * 100, 2),
            "projected_value": round(val, 2),
            "projected_gain": round(val - total_alloc, 2),
            "projected_gain_pct": round((val / total_alloc - 1) * 100, 2) if total_alloc > 0 else 0,
        })
    etf_projection.sort(key=lambda x: x["projected_gain"], reverse=True)

    return {
        "params": {
            "capital": capital,
            "dca": dca,
            "years": years,
            "n_simulations": n_simulations,
            "total_invested": round(total_invested, 2),
            "inflation_pct": inflation_pct,
            "target_amount": target_amount,
        },
        "calibration": {
            "mu_annual_pct": round(mu_annual * 100, 2),
            "sigma_annual_pct": round(sigma_annual * 100, 2),
            "history_years": history_years,
            "tickers": tickers,
        },
        "dates": dates,
        "percentiles": {
            "p5":  [round(float(x), 2) for x in p5],
            "p25": [round(float(x), 2) for x in p25],
            "p50": [round(float(x), 2) for x in p50],
            "p75": [round(float(x), 2) for x in p75],
            "p95": [round(float(x), 2) for x in p95],
        },
        "sample_paths": [[round(float(v), 2) for v in p] for p in sample_paths],
        "final": {
            "mean":    round(float(final_vals.mean()), 2),
            "p5":      round(float(np.percentile(final_vals, 5)), 2),
            "p25":     round(float(np.percentile(final_vals, 25)), 2),
            "p50":     round(float(np.percentile(final_vals, 50)), 2),
            "p75":     round(float(np.percentile(final_vals, 75)), 2),
            "p95":     round(float(np.percentile(final_vals, 95)), 2),
            "real_p50": round(real_p50, 2),
            "gain_p50": round(float(np.percentile(final_vals, 50)) - total_invested, 2),
            "gain_pct_p50": round((float(np.percentile(final_vals, 50)) / total_invested - 1) * 100, 2),
        },
        "probabilities": {
            "loss": round(prob_loss, 1),
            "target": round(prob_target, 1) if prob_target is not None else None,
            "target_amount": target_amount,
            "double": round(float((final_vals >= 2 * total_invested).mean() * 100), 1),
            "triple": round(float((final_vals >= 3 * total_invested).mean() * 100), 1),
        },
        "scenarios": scenarios,
        "yearly": yearly,
        "etf_projection": etf_projection,
    }


def _compute_scenarios(capital, dca, years, annual_rates):
    """Projection déterministe pour chaque scénario."""
    out = {}
    n = years * 12
    total_inv = capital + dca * n
    for name, rate in annual_rates.items():
        mr = (1 + rate) ** (1 / 12) - 1
        val = capital
        history = [capital]
        for _ in range(n):
            val = val * (1 + mr) + dca
            history.append(val)
        out[name] = {
            "final_value": round(val, 2),
            "gain": round(val - total_inv, 2),
            "gain_pct": round((val / total_inv - 1) * 100, 2),
            "annual_rate_pct": round(rate * 100, 2),
            "history": [round(v, 2) for v in history],
        }
    return out
