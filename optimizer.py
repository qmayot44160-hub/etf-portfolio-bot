"""
Optimisation des paramètres de stratégie par grid search + validation out-of-sample.

Workflow :
1. Split données : 70% train / 30% test (chronologique, jamais mélangé)
2. Grid search sur le train : sl_mult × tp_mult × risk_pct
3. Valider les N meilleures combinaisons sur le test (jamais vu pendant l'optim)
4. Résultat : table triée + score de robustesse + heatmap data

Un score robuste sur train ET test = edge réel.
Un bon score train mais mauvais test = overfitting.
"""

import itertools
import pandas as pd
from strategy_backtest import run_strategy_backtest


# Grille par défaut
DEFAULT_GRID = {
    "sl_atr_multiplier": [1.0, 1.5, 2.0, 2.5, 3.0],
    "tp_atr_multiplier": [1.5, 2.0, 2.5, 3.0, 3.5, 4.0],
    "risk_pct":          [0.5, 1.0, 2.0],
}


def _score(result: dict) -> float:
    """
    Score composite pour classer les combinaisons de paramètres.
    Favorise : Sharpe élevé × Profit Factor élevé × bon win rate.
    Pénalise : drawdown élevé et peu de trades.
    """
    if result.get("error"):
        return -999.0
    n = result.get("n_trades", 0)
    if n < 5:
        return -999.0
    sharpe = result.get("sharpe", 0) or 0
    pf     = min(result.get("profit_factor", 0) or 0, 10.0)  # cap à 10 (petit nb trades)
    dd     = abs(result.get("max_drawdown_pct", 0) or 0)
    wr     = (result.get("win_rate", 0) or 0) / 100
    # Pénalité drawdown exponentielle
    dd_penalty = max(0.1, 1 - dd / 100)
    return round(sharpe * pf * wr * dd_penalty, 4)


def _robustness(r_train: dict, r_test: dict) -> float:
    """
    Ratio score_test / score_train.
    1.0 = identique train/test (idéal).
    0.5 = moitié moins bon sur test (acceptable).
    < 0 = test négatif alors que train positif (overfitting sévère).
    """
    s_train = _score(r_train)
    if s_train <= 0:
        return 0.0
    return round(_score(r_test) / s_train, 3)


def _build_heatmap(results: list, sl_values: list, tp_values: list) -> list:
    """
    Matrice sl × tp → meilleur score (max sur risk_pct).
    Retourne une liste de {sl, tp, score} pour le rendu SVG côté JS.
    """
    matrix = {}
    for r in results:
        key = (r["_sl"], r["_tp"])
        if key not in matrix or r["score"] > matrix[key]:
            matrix[key] = r["score"]
    rows = []
    for sl in sl_values:
        for tp in tp_values:
            val = matrix.get((sl, tp))
            rows.append({"sl": sl, "tp": tp, "score": val})
    return rows


def run_optimization(
    df: pd.DataFrame,
    symbol: str,
    capital: float = 10_000.0,
    grid: dict = None,
    train_split: float = 0.70,
    top_n: int = 5,
) -> dict:
    """
    Lance le grid search + validation OOS.

    Paramètres
    ----------
    df           : DataFrame OHLCV complet
    symbol       : ex "BTC/USDT"
    capital      : capital initial pour la simulation
    grid         : dict de listes de valeurs à tester (DEFAULT_GRID si None)
    train_split  : fraction des données pour le train (0.70 = 70%)
    top_n        : nombre de combinaisons à valider sur le test

    Retourne
    --------
    dict complet avec grid_results, oos_validation, heatmap, best_params
    """
    grid = grid or DEFAULT_GRID
    n = len(df)
    split_idx = int(n * train_split)

    if split_idx < 150 or (n - split_idx) < 50:
        return {"error": f"Pas assez de données : {n} candles (minimum ~300)"}

    df_train = df.iloc[:split_idx].copy()
    df_test  = df.iloc[split_idx:].copy()

    sl_values   = grid.get("sl_atr_multiplier", [1.5])
    tp_values   = grid.get("tp_atr_multiplier", [3.0])
    risk_values = grid.get("risk_pct", [1.0])

    all_results = []
    for sl, tp, rsk in itertools.product(sl_values, tp_values, risk_values):
        if tp <= sl * 1.2:  # ratio minimum TP/SL = 1.2
            continue
        cfg = {"sl_atr_multiplier": sl, "tp_atr_multiplier": tp}
        r = run_strategy_backtest(df_train, symbol, capital, rsk, config=cfg)
        if r.get("error"):
            continue
        r["_sl"]     = sl
        r["_tp"]     = tp
        r["_rsk"]    = rsk
        r["score"]   = _score(r)
        r["params"]  = {"sl": sl, "tp": tp, "risk_pct": rsk}
        all_results.append(r)

    if not all_results:
        return {"error": "Aucun trade généré - vérifier les données ou élargir la grille"}

    all_results.sort(key=lambda x: x["score"], reverse=True)

    # --- Validation OOS des top_n combinaisons ---
    oos = []
    for r in all_results[:top_n]:
        p = r["params"]
        cfg_oos = {"sl_atr_multiplier": p["sl"], "tp_atr_multiplier": p["tp"]}
        r_oos = run_strategy_backtest(df_test, symbol, capital, p["risk_pct"], config=cfg_oos)
        oos.append({
            "params": p,
            "train": {
                "return_pct":    r.get("return_pct"),
                "win_rate":      r.get("win_rate"),
                "profit_factor": r.get("profit_factor"),
                "sharpe":        r.get("sharpe"),
                "max_dd":        r.get("max_drawdown_pct"),
                "n_trades":      r.get("n_trades"),
                "score":         r.get("score"),
            },
            "test": {
                "return_pct":    r_oos.get("return_pct"),
                "win_rate":      r_oos.get("win_rate"),
                "profit_factor": r_oos.get("profit_factor"),
                "sharpe":        r_oos.get("sharpe"),
                "max_dd":        r_oos.get("max_drawdown_pct"),
                "n_trades":      r_oos.get("n_trades"),
                "score":         _score(r_oos),
                "error":         r_oos.get("error"),
            },
            "robustness": _robustness(r, r_oos),
        })

    # --- Table légère pour l'UI ---
    table = [{
        "sl":           r["_sl"],
        "tp":           r["_tp"],
        "risk_pct":     r["_rsk"],
        "return_pct":   r.get("return_pct"),
        "win_rate":     r.get("win_rate"),
        "profit_factor":r.get("profit_factor"),
        "sharpe":       r.get("sharpe"),
        "max_dd":       r.get("max_drawdown_pct"),
        "n_trades":     r.get("n_trades"),
        "score":        r.get("score"),
    } for r in all_results[:50]]

    # Meilleurs params → appliquer directement au bot si robustesse >= 0.5
    best = all_results[0]
    best_robust = oos[0]["robustness"] if oos else 0
    recommendation = "valid" if best_robust >= 0.5 else ("weak" if best_robust >= 0.2 else "overfit")

    return {
        "symbol":          symbol,
        "n_total":         n,
        "train_candles":   split_idx,
        "test_candles":    n - split_idx,
        "train_split_pct": int(train_split * 100),
        "n_combinations":  len(all_results),
        "best_params":     best["params"],
        "best_score":      best["score"],
        "recommendation":  recommendation,   # "valid" | "weak" | "overfit"
        "grid_results":    table,
        "oos_validation":  oos,
        "heatmap":         _build_heatmap(all_results, sl_values, tp_values),
        "sl_values":       sl_values,
        "tp_values":       tp_values,
    }
