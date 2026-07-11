"""
Backtest walk-forward de la TradingStrategy sur donnees historiques OHLCV.

Principe :
- On avance candle par candle (walk-forward, pas de look-ahead)
- Pour chaque candle, on simule l'entree/sortie de trade
- SL/TP viennent directement de la strategie (ATR-based)
- Taille de position = risk_pct% du capital / distance au SL (risk fixe)
- Un seul trade ouvert a la fois
- Frais + slippage appliques sur chaque trade (realisme)
"""

import pandas as pd
import numpy as np
from trading_strategy import TradingStrategy, Signal

# Frais par defaut MEXC (maker/taker)
DEFAULT_FEE_PCT    = 0.10   # 0.10% par side
DEFAULT_SLIPPAGE   = 0.05   # 0.05% par side (market orders sur crypto)


def run_strategy_backtest(
    df: pd.DataFrame,
    symbol: str,
    initial_capital: float = 10_000.0,
    risk_pct: float = 1.0,
    min_warmup: int = 100,
    config: dict = None,
    fee_pct: float = DEFAULT_FEE_PCT,
    slippage_pct: float = DEFAULT_SLIPPAGE,
    trailing_stop: bool = False,
    trailing_stop_pct: float = 2.0,
    trailing_activation_pct: float = 1.0,
) -> dict:
    """
    Backtest walk-forward sur le DataFrame OHLCV fourni.

    Parametres
    ----------
    df                    : DataFrame OHLCV (open/high/low/close/volume)
    symbol                : nom du symbole (ex: BTC/USDT)
    initial_capital       : capital de depart en $
    risk_pct              : % du capital risque par trade (ex: 1.0 = 1%)
    min_warmup            : nombre de candles de chauffe avant le premier signal
    config                : config optionnelle pour TradingStrategy
    fee_pct               : frais de transaction par side en % (defaut MEXC = 0.10%)
    slippage_pct          : slippage estimé par side en % (defaut = 0.05%)
    trailing_stop         : activer le stop suiveur
    trailing_stop_pct     : distance du trailing stop en % depuis le pic (defaut 2%)
    trailing_activation_pct: gain minimum avant activation du trailing (defaut 1%)

    Retourne un dict avec metriques et serie equity.
    """
    # Cout total par side : frais + slippage
    cost_per_side = (fee_pct + slippage_pct) / 100.0
    strategy = TradingStrategy(config or {})
    capital = initial_capital
    equity_curve = [{"idx": min_warmup, "value": capital}]
    trades = []
    open_trade = None
    n = len(df)

    for i in range(min_warmup, n):
        window = df.iloc[: i + 1]
        row = df.iloc[i]
        close_price = float(row["close"])
        high_price = float(row["high"])
        low_price = float(row["low"])
        date_label = str(df.index[i]) if hasattr(df.index, "__iter__") else str(i)

        # ── Verifier SL/TP (+ trailing stop) sur le trade ouvert ──
        if open_trade:
            sl = open_trade["sl"]
            tp = open_trade["tp"]
            side = open_trade["side"]
            entry = open_trade["entry"]
            hit_sl = hit_tp = hit_trail = False
            exit_price = close_price
            exit_status = "SL_HIT"

            # -- Mise a jour du pic pour le trailing stop --
            if trailing_stop:
                if side == "BUY":
                    peak = max(open_trade.get("peak", entry), high_price)
                    open_trade["peak"] = peak
                    # Activation seulement apres gain suffisant
                    gain_pct = (peak - entry) / entry * 100
                    if gain_pct >= trailing_activation_pct:
                        trail_sl = peak * (1 - trailing_stop_pct / 100)
                        if trail_sl > sl:
                            open_trade["sl"] = trail_sl
                            sl = trail_sl
                else:  # SHORT
                    peak = min(open_trade.get("peak", entry), low_price)
                    open_trade["peak"] = peak
                    gain_pct = (entry - peak) / entry * 100
                    if gain_pct >= trailing_activation_pct:
                        trail_sl = peak * (1 + trailing_stop_pct / 100)
                        if trail_sl < sl:
                            open_trade["sl"] = trail_sl
                            sl = trail_sl

            if side == "BUY":
                # Pire cas d'abord : SL touche si le low passe en dessous
                if low_price <= sl:
                    hit_sl = True
                    exit_price = sl
                    # Distinguer trailing SL du SL initial
                    if trailing_stop and open_trade.get("peak") and sl > open_trade.get("initial_sl", sl):
                        exit_status = "TRAILING_SL"
                    else:
                        exit_status = "SL_HIT"
                elif high_price >= tp:
                    hit_tp = True
                    exit_price = tp
                    exit_status = "TP_HIT"
            else:  # SHORT
                if high_price >= sl:
                    hit_sl = True
                    exit_price = sl
                    if trailing_stop and open_trade.get("peak") and sl < open_trade.get("initial_sl", sl):
                        exit_status = "TRAILING_SL"
                    else:
                        exit_status = "SL_HIT"
                elif low_price <= tp:
                    hit_tp = True
                    exit_price = tp
                    exit_status = "TP_HIT"

            if hit_sl or hit_tp:
                qty = open_trade["qty"]
                if side == "BUY":
                    pnl = (exit_price - entry) * qty
                else:
                    pnl = (entry - exit_price) * qty
                # Frais + slippage : appliqués sur la valeur notionnelle (entry + exit)
                notional = (entry + exit_price) * qty
                cost = notional * cost_per_side
                pnl -= cost
                capital += pnl
                capital = max(capital, 0.01)  # eviter capital negatif

                trades.append({
                    "entry_date": open_trade["entry_date"],
                    "exit_date": date_label,
                    "side": side,
                    "entry": round(entry, 4),
                    "exit": round(exit_price, 4),
                    "sl": round(open_trade.get("initial_sl", sl), 4),
                    "tp": round(tp, 4),
                    "qty": round(qty, 6),
                    "pnl": round(pnl, 4),
                    "pnl_pct": round(pnl / (entry * qty) * 100, 2) if entry * qty > 0 else 0,
                    "fee": round(cost, 4),
                    "status": exit_status,
                    "signal": open_trade["signal"],
                    "confidence": open_trade["confidence"],
                })
                open_trade = None
                equity_curve.append({"idx": i, "value": round(capital, 2)})

        # ── Ouvrir un nouveau trade si aucun en cours ──
        if open_trade is None:
            try:
                sig = strategy.analyze(window, symbol)
            except Exception:
                continue

            if sig.signal not in (Signal.BUY, Signal.STRONG_BUY,
                                  Signal.SELL, Signal.STRONG_SELL):
                continue

            sl_dist = abs(sig.price - sig.stop_loss)
            if sl_dist <= 0 or sig.price <= 0:
                continue

            risk_amount = capital * (risk_pct / 100)
            qty = risk_amount / sl_dist
            if qty <= 0:
                continue

            side = "BUY" if sig.signal in (Signal.BUY, Signal.STRONG_BUY) else "SELL"
            open_trade = {
                "entry": sig.price,
                "sl": sig.stop_loss,
                "initial_sl": sig.stop_loss,   # reference fixe pour detecter le trail
                "tp": sig.take_profit,
                "side": side,
                "qty": qty,
                "peak": sig.price,             # pic depuis l'entree (pour trailing)
                "entry_date": date_label,
                "signal": sig.signal.value,
                "confidence": sig.confidence,
            }

    # ── Cloturer position ouverte au dernier candle ──
    if open_trade:
        last_close = float(df.iloc[-1]["close"])
        entry = open_trade["entry"]
        qty = open_trade["qty"]
        side = open_trade["side"]
        pnl = (last_close - entry) * qty if side == "BUY" else (entry - last_close) * qty
        notional = (entry + last_close) * qty
        cost = notional * cost_per_side
        pnl -= cost
        capital += pnl
        trades.append({
            "entry_date": open_trade["entry_date"],
            "exit_date": "OPEN",
            "side": side,
            "entry": round(entry, 4),
            "exit": round(last_close, 4),
            "sl": round(open_trade["sl"], 4),
            "tp": round(open_trade["tp"], 4),
            "qty": round(qty, 6),
            "pnl": round(pnl, 4),
            "pnl_pct": round(pnl / (entry * qty) * 100, 2) if entry * qty > 0 else 0,
            "fee": round(cost, 4),
            "status": "STILL_OPEN",
            "signal": open_trade["signal"],
            "confidence": open_trade["confidence"],
        })

    equity_curve.append({"idx": n - 1, "value": round(capital, 2)})

    if not trades:
        return {
            "error": "Aucun trade genere sur cette periode",
            "n_candles": n,
            "n_warmup": min_warmup,
        }

    # ── Calcul des metriques ──
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    total_fees = round(sum(t.get("fee", 0) for t in trades), 4)

    win_rate = len(wins) / len(trades) * 100
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 99.0
    avg_win = gross_profit / len(wins) if wins else 0
    avg_loss = gross_loss / len(losses) if losses else 0
    expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

    # Drawdown sur equity curve
    eq_vals = pd.Series([e["value"] for e in equity_curve])
    running_max = eq_vals.cummax()
    dd_series = (eq_vals / running_max - 1) * 100
    max_dd = float(dd_series.min())

    # Ratio Sharpe simplifie sur les PnL
    pnl_series = pd.Series([t["pnl"] for t in trades])
    sharpe = 0.0
    if len(pnl_series) > 2 and pnl_series.std() > 0:
        sharpe = round(pnl_series.mean() / pnl_series.std() * np.sqrt(len(pnl_series)), 2)

    # Consecutive wins/losses
    max_consec_wins = _max_consecutive(trades, win=True)
    max_consec_losses = _max_consecutive(trades, win=False)

    # Buy-and-hold benchmark : premier et dernier close sur la periode tradee
    first_close = float(df.iloc[min_warmup]["close"])
    last_close_bh = float(df.iloc[-1]["close"])
    bh_return_pct = round((last_close_bh / first_close - 1) * 100, 2) if first_close > 0 else 0.0

    # Trailing stop exits count
    n_trailing = sum(1 for t in trades if t.get("status") == "TRAILING_SL")

    # Monte Carlo : automatique si assez de trades. Bootstrap par défaut.
    mc = None
    if len(trades) >= 10:
        try:
            mc = monte_carlo_analysis(trades, initial_capital, n_simulations=1000, method="bootstrap")
        except Exception:
            mc = None

    return {
        "symbol": symbol,
        "n_candles": n,
        "n_warmup": min_warmup,
        "n_trades": len(trades),
        "win_rate": round(win_rate, 1),
        "profit_factor": profit_factor,
        "sharpe": sharpe,
        "total_pnl": round(sum(t["pnl"] for t in trades), 4),
        "return_pct": round((capital / initial_capital - 1) * 100, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "expectancy": round(expectancy, 4),
        "max_consec_wins": max_consec_wins,
        "max_consec_losses": max_consec_losses,
        "initial_capital": initial_capital,
        "final_capital": round(capital, 2),
        "total_fees": total_fees,
        "fee_pct": fee_pct,
        "slippage_pct": slippage_pct,
        "trailing_stop": trailing_stop,
        "trailing_stop_pct": trailing_stop_pct,
        "n_trailing_exits": n_trailing,
        "bh_return_pct": bh_return_pct,
        "monte_carlo": mc,
        "trades": trades[-100:],  # 100 derniers
        "equity_curve": equity_curve,
    }


def _max_consecutive(trades: list, win: bool) -> int:
    """Compte la serie max de trades gagnants (win=True) ou perdants (win=False)."""
    max_run = cur = 0
    for t in trades:
        is_win = t["pnl"] > 0
        if is_win == win:
            cur += 1
            max_run = max(max_run, cur)
        else:
            cur = 0
    return max_run


def monte_carlo_analysis(
    trades: list,
    initial_capital: float,
    n_simulations: int = 1000,
    method: str = "bootstrap",
    seed: int = 42,
) -> dict:
    """
    Analyse Monte Carlo : ré-échantillonne les trades pour estimer la dispersion
    des résultats. Distingue chance vs edge.

    method = "bootstrap" : tirage avec remise (teste la robustesse de l'edge)
    method = "shuffle"   : permutation sans remise (teste la sensibilité à l'ordre)

    Retourne percentiles return%, max drawdown%, et proba d'être profitable.
    """
    if not trades or len(trades) < 5:
        return {"error": "Trop peu de trades pour Monte Carlo (minimum 5)"}

    rng = np.random.default_rng(seed)
    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    n = len(pnls)
    final_returns = np.zeros(n_simulations)
    max_dds = np.zeros(n_simulations)

    for i in range(n_simulations):
        if method == "bootstrap":
            sample = rng.choice(pnls, size=n, replace=True)
        else:  # shuffle
            sample = pnls.copy()
            rng.shuffle(sample)

        # Reconstruction de l'equity
        equity = initial_capital + np.cumsum(sample)
        equity = np.maximum(equity, 0.01)  # pas de capital negatif
        running_max = np.maximum.accumulate(equity)
        dd = (equity / running_max - 1) * 100
        final_returns[i] = (equity[-1] / initial_capital - 1) * 100
        max_dds[i] = dd.min()

    pct = lambda arr, p: float(np.percentile(arr, p))
    prob_profitable = float((final_returns > 0).sum() / n_simulations * 100)
    prob_dd_over_20 = float((max_dds < -20).sum() / n_simulations * 100)

    return {
        "n_simulations": n_simulations,
        "method": method,
        "return_p5":  round(pct(final_returns,  5), 2),
        "return_p25": round(pct(final_returns, 25), 2),
        "return_p50": round(pct(final_returns, 50), 2),
        "return_p75": round(pct(final_returns, 75), 2),
        "return_p95": round(pct(final_returns, 95), 2),
        "dd_p5":  round(pct(max_dds,  5), 2),   # pire 5%
        "dd_p25": round(pct(max_dds, 25), 2),
        "dd_p50": round(pct(max_dds, 50), 2),
        "dd_p75": round(pct(max_dds, 75), 2),
        "dd_p95": round(pct(max_dds, 95), 2),   # meilleur 5%
        "prob_profitable":  round(prob_profitable, 1),
        "prob_dd_over_20":  round(prob_dd_over_20, 1),
        # Histogramme des returns : 20 bins pour le frontend
        "histogram": _histogram(final_returns, 20),
    }


def _histogram(values: np.ndarray, n_bins: int) -> list:
    """Retourne une liste de {x, count} pour le rendu SVG du frontend."""
    counts, edges = np.histogram(values, bins=n_bins)
    bins = []
    for i in range(n_bins):
        bins.append({
            "x_min": round(float(edges[i]), 2),
            "x_max": round(float(edges[i + 1]), 2),
            "count": int(counts[i]),
        })
    return bins
