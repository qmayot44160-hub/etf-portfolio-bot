"""
Backtest walk-forward de la TradingStrategy sur donnees historiques OHLCV.

Principe :
- On avance candle par candle (walk-forward, pas de look-ahead)
- Pour chaque candle, on simule l'entree/sortie de trade
- SL/TP viennent directement de la strategie (ATR-based)
- Taille de position = risk_pct% du capital / distance au SL (risk fixe)
- Un seul trade ouvert a la fois
"""

import pandas as pd
import numpy as np
from trading_strategy import TradingStrategy, Signal


def run_strategy_backtest(
    df: pd.DataFrame,
    symbol: str,
    initial_capital: float = 10_000.0,
    risk_pct: float = 1.0,
    min_warmup: int = 100,
    config: dict = None,
) -> dict:
    """
    Backtest walk-forward sur le DataFrame OHLCV fourni.

    Parametres
    ----------
    df             : DataFrame OHLCV (open/high/low/close/volume)
    symbol         : nom du symbole (ex: BTC/USDT)
    initial_capital: capital de depart en $
    risk_pct       : % du capital risque par trade (ex: 1.0 = 1%)
    min_warmup     : nombre de candles de chauffe avant le premier signal
    config         : config optionnelle pour TradingStrategy

    Retourne un dict avec metriques et serie equity.
    """
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

        # ── Verifier SL/TP sur le trade ouvert ──
        if open_trade:
            sl = open_trade["sl"]
            tp = open_trade["tp"]
            side = open_trade["side"]
            hit_sl = hit_tp = False
            exit_price = close_price

            if side == "BUY":
                # Pire cas d'abord : SL touche si le low passe en dessous
                if low_price <= sl:
                    hit_sl = True
                    exit_price = sl
                elif high_price >= tp:
                    hit_tp = True
                    exit_price = tp
            else:  # SHORT
                if high_price >= sl:
                    hit_sl = True
                    exit_price = sl
                elif low_price <= tp:
                    hit_tp = True
                    exit_price = tp

            if hit_sl or hit_tp:
                entry = open_trade["entry"]
                qty = open_trade["qty"]
                if side == "BUY":
                    pnl = (exit_price - entry) * qty
                else:
                    pnl = (entry - exit_price) * qty
                capital += pnl
                capital = max(capital, 0.01)  # eviter capital negatif

                trades.append({
                    "entry_date": open_trade["entry_date"],
                    "exit_date": date_label,
                    "side": side,
                    "entry": round(entry, 4),
                    "exit": round(exit_price, 4),
                    "sl": round(sl, 4),
                    "tp": round(tp, 4),
                    "qty": round(qty, 6),
                    "pnl": round(pnl, 4),
                    "pnl_pct": round(pnl / (entry * qty) * 100, 2) if entry * qty > 0 else 0,
                    "status": "TP_HIT" if hit_tp else "SL_HIT",
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
                "tp": sig.take_profit,
                "side": side,
                "qty": qty,
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
