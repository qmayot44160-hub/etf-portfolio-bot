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
from strategy_backtest import run_strategy_backtest, DEFAULT_FEE_PCT, DEFAULT_SLIPPAGE


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


def run_walk_forward(
    df: pd.DataFrame,
    symbol: str,
    capital: float = 10_000.0,
    grid: dict = None,
    n_windows: int = 5,
    train_pct: float = 0.7,
    fee_pct: float = DEFAULT_FEE_PCT,
    slippage_pct: float = DEFAULT_SLIPPAGE,
) -> dict:
    """
    Walk-forward optimization : fenêtres glissantes train→test successives.

    Au lieu d'un seul split 70/30, on découpe en N segments. Pour chaque segment :
      - Train sur train_pct% des candles → meilleurs params
      - Test sur les (1-train_pct)% suivants → réalité
    On agrège les performances test de chaque fenêtre.

    Le walk-forward est LE test de robustesse temporelle : si l'edge tient sur 5
    périodes successives (pas une seule), c'est un edge réel et non un overfit.
    """
    grid = grid or DEFAULT_GRID
    bt_kwargs = {"fee_pct": fee_pct, "slippage_pct": slippage_pct}
    n = len(df)

    # Taille minimum d'une fenêtre : 200 candles (150 train + 50 test)
    window_size = n // n_windows
    if window_size < 200:
        return {"error": f"Pas assez de donnees pour {n_windows} fenetres "
                         f"(besoin {n_windows * 200} candles, dispo {n})"}

    sl_values   = grid.get("sl_atr_multiplier", [1.5])
    tp_values   = grid.get("tp_atr_multiplier", [3.0])
    risk_values = grid.get("risk_pct", [1.0])

    windows = []
    for w in range(n_windows):
        start = w * window_size
        end = min(start + window_size, n)
        win_df = df.iloc[start:end].copy()
        train_idx = int(len(win_df) * train_pct)
        win_train = win_df.iloc[:train_idx]
        win_test  = win_df.iloc[train_idx:]

        # Grid search sur train de cette fenêtre
        best = None
        for sl, tp, rsk in itertools.product(sl_values, tp_values, risk_values):
            if tp <= sl * 1.2:
                continue
            cfg = {"sl_atr_multiplier": sl, "tp_atr_multiplier": tp}
            r = run_strategy_backtest(win_train, symbol, capital, rsk, config=cfg, **bt_kwargs)
            if r.get("error"):
                continue
            r["params"] = {"sl": sl, "tp": tp, "risk_pct": rsk}
            r["score"] = _score(r)
            if best is None or r["score"] > best["score"]:
                best = r

        if not best:
            windows.append({
                "window": w + 1,
                "train_candles": train_idx,
                "test_candles":  len(win_test),
                "error": "Aucun trade train",
            })
            continue

        # Test sur la fenêtre OOS avec les meilleurs params train
        p = best["params"]
        cfg_test = {"sl_atr_multiplier": p["sl"], "tp_atr_multiplier": p["tp"]}
        r_test = run_strategy_backtest(win_test, symbol, capital, p["risk_pct"], config=cfg_test, **bt_kwargs)

        windows.append({
            "window":        w + 1,
            "train_candles": train_idx,
            "test_candles":  len(win_test),
            "params":        p,
            "train_return":  best.get("return_pct"),
            "train_score":   best.get("score"),
            "train_trades":  best.get("n_trades"),
            "test_return":   r_test.get("return_pct") if not r_test.get("error") else None,
            "test_win_rate": r_test.get("win_rate") if not r_test.get("error") else None,
            "test_trades":   r_test.get("n_trades") if not r_test.get("error") else 0,
            "test_dd":       r_test.get("max_drawdown_pct") if not r_test.get("error") else None,
            "test_pf":       r_test.get("profit_factor") if not r_test.get("error") else None,
            "test_error":    r_test.get("error"),
        })

    # Agrégation : moyenne return test, % fenêtres gagnantes, std return
    valid = [w for w in windows if w.get("test_return") is not None]
    n_valid = len(valid)
    n_winners = sum(1 for w in valid if w["test_return"] > 0)
    avg_test_return = (sum(w["test_return"] for w in valid) / n_valid) if n_valid else 0
    avg_test_dd = (sum(w["test_dd"] for w in valid) / n_valid) if n_valid else 0
    consistency_pct = (n_winners / n_valid * 100) if n_valid else 0

    # Std des retours test : indicateur de stabilité
    if n_valid > 1:
        mean_r = avg_test_return
        var = sum((w["test_return"] - mean_r) ** 2 for w in valid) / n_valid
        std_test_return = var ** 0.5
    else:
        std_test_return = 0

    # Verdict global
    if consistency_pct >= 70 and avg_test_return > 0:
        verdict = "robust"
    elif consistency_pct >= 50:
        verdict = "mixed"
    else:
        verdict = "fragile"

    return {
        "symbol":          symbol,
        "n_windows":       n_windows,
        "window_size":     window_size,
        "train_pct":       train_pct,
        "windows":         windows,
        "n_valid":         n_valid,
        "n_winners":       n_winners,
        "consistency_pct": round(consistency_pct, 1),
        "avg_test_return": round(avg_test_return, 2),
        "std_test_return": round(std_test_return, 2),
        "avg_test_dd":     round(avg_test_dd, 2),
        "verdict":         verdict,
        "fee_pct":         fee_pct,
        "slippage_pct":    slippage_pct,
    }


def run_optimization(
    df: pd.DataFrame,
    symbol: str,
    capital: float = 10_000.0,
    grid: dict = None,
    train_split: float = 0.70,
    top_n: int = 5,
    fee_pct: float = DEFAULT_FEE_PCT,
    slippage_pct: float = DEFAULT_SLIPPAGE,
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
    # Paramètres réalisme transmis à chaque backtest
    bt_kwargs = {"fee_pct": fee_pct, "slippage_pct": slippage_pct}
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
        r = run_strategy_backtest(df_train, symbol, capital, rsk, config=cfg, **bt_kwargs)
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
        r_oos = run_strategy_backtest(df_test, symbol, capital, p["risk_pct"], config=cfg_oos, **bt_kwargs)
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
        "fee_pct":         fee_pct,
        "slippage_pct":    slippage_pct,
        "grid_results":    table,
        "oos_validation":  oos,
        "heatmap":         _build_heatmap(all_results, sl_values, tp_values),
        "sl_values":       sl_values,
        "tp_values":       tp_values,
    }
