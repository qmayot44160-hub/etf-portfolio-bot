"""
Module de backtest : simule la performance historique du portefeuille.
"""

import pandas as pd
import numpy as np
from config import PORTFOLIO, INITIAL_CAPITAL, DCA_MONTHLY
from market_data import get_historical_data


def run_backtest(capital: float = None, dca: float = None, years: int = None) -> dict:
    """
    Simule le portefeuille sur l'historique avec DCA mensuel.
    Retourne les métriques de performance.
    """
    if capital is None:
        capital = INITIAL_CAPITAL
    if dca is None:
        dca = DCA_MONTHLY

    prices = get_historical_data(years)
    if prices.empty:
        return {"error": "Pas de données historiques disponibles"}

    weights = {t: cfg["target_pct"] / 100 for t, cfg in PORTFOLIO.items()}
    tickers = [t for t in weights if t in prices.columns]

    if not tickers:
        return {"error": "Aucun ticker trouvé dans les données"}

    # Normaliser les poids aux tickers disponibles
    total_w = sum(weights[t] for t in tickers)
    w = {t: weights[t] / total_w for t in tickers}

    prices = prices[tickers]
    returns = prices.pct_change().dropna()

    # Simulation jour par jour
    portfolio_values = []
    total_invested_series = []
    cash = capital
    total_invested = capital
    shares = {t: (capital * w[t]) / prices.iloc[0][t] for t in tickers}
    last_dca_month = prices.index[0].month

    for date, row in prices.iterrows():
        # DCA mensuel
        if date.month != last_dca_month:
            for t in tickers:
                buy_amount = dca * w[t]
                new_shares = buy_amount / row[t]
                shares[t] += new_shares
            total_invested += dca
            last_dca_month = date.month

        # Valeur du portefeuille
        value = sum(shares[t] * row[t] for t in tickers)
        portfolio_values.append({"date": date, "value": value})
        total_invested_series.append({"date": date, "invested": total_invested})

    df = pd.DataFrame(portfolio_values).set_index("date")
    df_invested = pd.DataFrame(total_invested_series).set_index("date")

    # Métriques
    total_return = (df["value"].iloc[-1] / df["value"].iloc[0] - 1) * 100
    daily_returns = df["value"].pct_change().dropna()
    annualized_return = ((1 + daily_returns.mean()) ** 252 - 1) * 100
    volatility = daily_returns.std() * np.sqrt(252) * 100
    sharpe = annualized_return / volatility if volatility > 0 else 0
    max_dd = ((df["value"] / df["value"].cummax()) - 1).min() * 100

    gain = df["value"].iloc[-1] - total_invested
    gain_pct = (df["value"].iloc[-1] / total_invested - 1) * 100

    return {
        "start_date": str(df.index[0].date()),
        "end_date": str(df.index[-1].date()),
        "initial_capital": capital,
        "total_invested": round(total_invested, 2),
        "final_value": round(df["value"].iloc[-1], 2),
        "gain": round(gain, 2),
        "gain_pct": round(gain_pct, 2),
        "total_return_pct": round(total_return, 2),
        "annualized_return_pct": round(annualized_return, 2),
        "volatility_pct": round(volatility, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "portfolio_history": df.reset_index().to_dict(orient="records"),
        "invested_history": df_invested.reset_index().to_dict(orient="records"),
    }
