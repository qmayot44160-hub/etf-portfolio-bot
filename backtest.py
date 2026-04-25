"""
Module de backtest avancé - simulation institutionnelle du portefeuille ETF.

Fonctionnalités :
- Simulation DCA mensuel avec/sans rééquilibrage
- Comparaison vs benchmark (S&P 500 buy & hold)
- Métriques avancées : Sortino, Calmar, VaR, best/worst year
- Heatmap des rendements mensuels
- Drawdown timeline
- Contribution par ETF
- Analyse de scénarios (krach 2008, COVID 2020, hausse des taux 2022)
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from config import PORTFOLIO, INITIAL_CAPITAL, DCA_MONTHLY, REBALANCE_THRESHOLD_PCT
from market_data import get_historical_data
import yfinance as yf


BENCHMARK_TICKER = "SPY"   # S&P 500 comme référence universelle


# ─────────────────────────────────────────────────────────
#  UTILITAIRES
# ─────────────────────────────────────────────────────────

def _sharpe(returns: pd.Series, rf: float = 0.02) -> float:
    ann = (1 + returns.mean()) ** 252 - 1
    vol = returns.std() * np.sqrt(252)
    return round((ann - rf) / vol, 2) if vol > 0 else 0


def _sortino(returns: pd.Series, rf: float = 0.02) -> float:
    ann = (1 + returns.mean()) ** 252 - 1
    down = returns[returns < 0]
    dstd = down.std() * np.sqrt(252)
    return round((ann - rf) / dstd, 2) if dstd > 0 else 0


def _max_drawdown(values: pd.Series) -> float:
    dd = (values / values.cummax()) - 1
    return round(float(dd.min()) * 100, 2)


def _calmar(returns: pd.Series, values: pd.Series) -> float:
    ann = (1 + returns.mean()) ** 252 - 1
    mdd = abs(_max_drawdown(values) / 100)
    return round(ann / mdd, 2) if mdd > 0 else 0


def _var_95(returns: pd.Series) -> float:
    return round(float(np.percentile(returns.dropna(), 5)) * 100, 2)


def _cvar_95(returns: pd.Series) -> float:
    pct5 = np.percentile(returns.dropna(), 5)
    return round(float(returns[returns <= pct5].mean()) * 100, 2)


# ─────────────────────────────────────────────────────────
#  SIMULATION PRINCIPALE
# ─────────────────────────────────────────────────────────

def run_backtest(
    capital: float = None,
    dca: float = None,
    years: int = None,
    rebalance: bool = True,
    rebalance_threshold: float = None,
    custom_weights: dict = None,
) -> dict:
    """
    Backtest complet avec toutes les métriques institutionnelles.
    Retourne portfolio, benchmark, drawdown, heatmap, contribution par ETF.
    """
    if capital is None:
        capital = INITIAL_CAPITAL
    if dca is None:
        dca = DCA_MONTHLY
    if rebalance_threshold is None:
        rebalance_threshold = REBALANCE_THRESHOLD_PCT

    prices = get_historical_data(years)
    if prices.empty:
        return {"error": "Pas de données historiques disponibles"}

    weights = custom_weights or {t: cfg["target_pct"] / 100 for t, cfg in PORTFOLIO.items()}
    tickers = [t for t in weights if t in prices.columns]

    if not tickers:
        return {"error": "Aucun ticker trouvé dans les données"}

    total_w = sum(weights[t] for t in tickers)
    w = {t: weights[t] / total_w for t in tickers}
    prices = prices[tickers].copy()

    # ── Simulation DCA + rééquilibrage ──────────────────
    portfolio_values = []
    total_invested_series = []
    total_invested = capital
    cash = 0
    shares = {t: (capital * w[t]) / float(prices.iloc[0][t]) for t in tickers}
    last_dca_month = prices.index[0].month
    rebalance_count = 0

    for date, row in prices.iterrows():
        # DCA mensuel
        if date.month != last_dca_month:
            for t in tickers:
                buy_amount = dca * w[t]
                shares[t] += buy_amount / float(row[t])
            total_invested += dca
            last_dca_month = date.month

        # Valeur totale
        value = sum(shares[t] * float(row[t]) for t in tickers)

        # Rééquilibrage si activé
        if rebalance and len(portfolio_values) > 0 and date.month != prices.index[0].month:
            for t in tickers:
                current_pct = (shares[t] * float(row[t]) / value) * 100
                target_pct = w[t] * 100
                if abs(current_pct - target_pct) >= rebalance_threshold:
                    target_val = value * w[t]
                    current_val = shares[t] * float(row[t])
                    diff = target_val - current_val
                    shares[t] += diff / float(row[t])
                    rebalance_count += 1
                    break  # un rééquilibrage max par jour

        portfolio_values.append({"date": date, "value": value})
        total_invested_series.append({"date": date, "invested": total_invested})

    df = pd.DataFrame(portfolio_values).set_index("date")
    df_inv = pd.DataFrame(total_invested_series).set_index("date")

    # ── Benchmark S&P 500 ────────────────────────────────
    benchmark_data = _get_benchmark(prices.index[0], prices.index[-1], capital, dca)

    # ── Métriques ────────────────────────────────────────
    daily_ret = df["value"].pct_change().dropna()
    final_val = float(df["value"].iloc[-1])
    gain = final_val - total_invested
    gain_pct = (final_val / total_invested - 1) * 100
    ann_ret = ((1 + daily_ret.mean()) ** 252 - 1) * 100
    volatility = daily_ret.std() * np.sqrt(252) * 100

    # Best / worst year
    yearly = df["value"].resample("YE").last().pct_change().dropna() * 100
    best_year = {"year": int(yearly.idxmax().year), "return": round(float(yearly.max()), 1)} if len(yearly) > 0 else None
    worst_year = {"year": int(yearly.idxmin().year), "return": round(float(yearly.min()), 1)} if len(yearly) > 0 else None

    # % mois positifs
    monthly_ret = df["value"].resample("ME").last().pct_change().dropna()
    pct_pos_months = round(float((monthly_ret > 0).mean()) * 100, 1)

    # Drawdown series
    dd_series = ((df["value"] / df["value"].cummax()) - 1) * 100
    drawdown_history = [
        {"date": str(d.date()), "dd": round(float(v), 2)}
        for d, v in dd_series.items()
    ]

    # Heatmap rendements mensuels
    monthly_heatmap = _build_monthly_heatmap(df["value"])

    # Contribution par ETF
    etf_contribution = _etf_contribution(prices, shares, w, tickers)

    # Simulation sans rééquilibrage pour comparaison
    no_rebal = None
    if rebalance:
        no_rebal_result = run_backtest(capital, dca, years, rebalance=False, custom_weights=custom_weights)
        if "error" not in no_rebal_result:
            no_rebal = {
                "final_value": no_rebal_result["final_value"],
                "gain_pct": no_rebal_result["gain_pct"],
                "sharpe_ratio": no_rebal_result["sharpe_ratio"],
                "max_drawdown_pct": no_rebal_result["max_drawdown_pct"],
            }

    return {
        "start_date": str(df.index[0].date()),
        "end_date": str(df.index[-1].date()),
        "initial_capital": capital,
        "monthly_dca": dca,
        "total_invested": round(total_invested, 2),
        "final_value": round(final_val, 2),
        "gain": round(gain, 2),
        "gain_pct": round(gain_pct, 2),
        "annualized_return_pct": round(ann_ret, 2),
        "volatility_pct": round(volatility, 2),
        "sharpe_ratio": _sharpe(daily_ret),
        "sortino_ratio": _sortino(daily_ret),
        "calmar_ratio": _calmar(daily_ret, df["value"]),
        "max_drawdown_pct": _max_drawdown(df["value"]),
        "var_95_pct": _var_95(daily_ret),
        "cvar_95_pct": _cvar_95(daily_ret),
        "best_year": best_year,
        "worst_year": worst_year,
        "pct_positive_months": pct_pos_months,
        "rebalance_count": rebalance_count,
        # Séries temporelles
        "portfolio_history": [
            {"date": str(r["date"].date()), "value": round(r["value"], 2)}
            for r in portfolio_values
        ],
        "invested_history": [
            {"date": str(r["date"].date()), "invested": round(r["invested"], 2)}
            for r in total_invested_series
        ],
        "drawdown_history": drawdown_history,
        # Charts
        "monthly_heatmap": monthly_heatmap,
        "etf_contribution": etf_contribution,
        # Benchmark
        "benchmark": benchmark_data,
        # Comparaison avec/sans rééquilibrage
        "without_rebalance": no_rebal,
        "rebalance_enabled": rebalance,
    }


# ─────────────────────────────────────────────────────────
#  BENCHMARK
# ─────────────────────────────────────────────────────────

def _get_benchmark(start, end, capital: float, dca: float) -> dict:
    """Simule un buy & hold S&P 500 avec DCA pour comparaison."""
    try:
        data = yf.download(BENCHMARK_TICKER, start=start, end=end, progress=False)
        if data.empty:
            return None

        close = data["Close"] if "Close" in data.columns else data.iloc[:, 0]
        close = close.dropna()

        shares = capital / float(close.iloc[0])
        total_inv = capital
        last_month = close.index[0].month
        values = []

        for date, price in close.items():
            if date.month != last_month:
                shares += dca / float(price)
                total_inv += dca
                last_month = date.month
            values.append({"date": str(date.date()), "value": round(float(shares * price), 2)})

        final = float(shares * close.iloc[-1])
        daily_ret = close.pct_change().dropna()
        ann_ret = ((1 + daily_ret.mean()) ** 252 - 1) * 100
        dd = ((close / close.cummax()) - 1).min() * 100

        return {
            "name": "S&P 500 (SPY)",
            "final_value": round(final, 2),
            "gain_pct": round((final / total_inv - 1) * 100, 2),
            "annualized_return_pct": round(float(ann_ret), 2),
            "sharpe_ratio": _sharpe(daily_ret),
            "max_drawdown_pct": round(float(dd), 2),
            "history": values,
        }
    except Exception as e:
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────
#  HEATMAP RENDEMENTS MENSUELS
# ─────────────────────────────────────────────────────────

def _build_monthly_heatmap(values: pd.Series) -> dict:
    """Construit une matrice année × mois des rendements."""
    monthly = values.resample("ME").last().pct_change().dropna() * 100
    months = ["Jan","Fév","Mar","Avr","Mai","Jun","Jul","Aoû","Sep","Oct","Nov","Déc"]
    years = sorted(monthly.index.year.unique())
    matrix = []
    for year in years:
        row = []
        for m in range(1, 13):
            idx = monthly[(monthly.index.year == year) & (monthly.index.month == m)]
            row.append(round(float(idx.iloc[0]), 2) if len(idx) > 0 else None)
        matrix.append({"year": int(year), "months": row})
    return {"months": months, "data": matrix}


# ─────────────────────────────────────────────────────────
#  CONTRIBUTION PAR ETF
# ─────────────────────────────────────────────────────────

def _etf_contribution(prices: pd.DataFrame, final_shares: dict,
                       weights: dict, tickers: list) -> list:
    """Calcule la performance individuelle de chaque ETF."""
    result = []
    for t in tickers:
        try:
            p0 = float(prices[t].iloc[0])
            p1 = float(prices[t].iloc[-1])
            ret = (p1 / p0 - 1) * 100
            contribution = ret * weights[t]
            name = PORTFOLIO.get(t, {}).get("name", t)
            result.append({
                "ticker": t,
                "name": name,
                "weight": round(weights[t] * 100, 1),
                "return_pct": round(ret, 2),
                "contribution_pct": round(contribution, 2),
            })
        except Exception:
            pass
    return sorted(result, key=lambda x: x["contribution_pct"], reverse=True)
