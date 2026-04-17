"""
Paper trading engine — simule les ordres sans exécution réelle.

Persiste dans paper_trades.json. Utilisé quand settings.paper_mode = True.
"""

import os
import json
import time
from datetime import datetime
from data_paths import data_path

PAPER_FILE = data_path("paper_trades.json")
PAPER_STATE_FILE = data_path("paper_state.json")


def _load_state() -> dict:
    if os.path.exists(PAPER_STATE_FILE):
        try:
            with open(PAPER_STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"cash": 10000.0, "positions": {}}


def _save_state(state: dict):
    with open(PAPER_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _load_trades() -> list:
    if os.path.exists(PAPER_FILE):
        try:
            with open(PAPER_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_trades(trades: list):
    with open(PAPER_FILE, "w") as f:
        json.dump(trades, f, indent=2)


def simulate_order(symbol: str, side: str, quantity: float, price: float,
                   order_type: str = "market", broker: str = "paper") -> dict:
    """
    Simule un ordre : débite/crédite le cash paper, met à jour les positions,
    logue le trade. Retourne un dict compatible avec la plupart des brokers.
    """
    state = _load_state()
    trades = _load_trades()

    side = side.upper()
    cost = price * quantity
    now = datetime.now().isoformat(timespec="seconds")

    if side == "BUY":
        if state["cash"] < cost:
            return {"status": "rejected", "error": f"Paper cash insuffisant ({state['cash']:.2f} < {cost:.2f})"}
        state["cash"] -= cost
        pos = state["positions"].get(symbol, {"quantity": 0, "avg_price": 0})
        new_qty = pos["quantity"] + quantity
        pos["avg_price"] = (pos["avg_price"] * pos["quantity"] + cost) / new_qty if new_qty > 0 else price
        pos["quantity"] = new_qty
        state["positions"][symbol] = pos
    elif side == "SELL":
        pos = state["positions"].get(symbol, {"quantity": 0, "avg_price": 0})
        if pos["quantity"] < quantity:
            return {"status": "rejected", "error": f"Paper position insuffisante ({pos['quantity']} < {quantity})"}
        pos["quantity"] -= quantity
        state["cash"] += cost
        if pos["quantity"] <= 1e-9:
            state["positions"].pop(symbol, None)
        else:
            state["positions"][symbol] = pos
    else:
        return {"status": "rejected", "error": f"Side invalide: {side}"}

    order = {
        "id": f"paper-{int(time.time() * 1000)}",
        "timestamp": now,
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "price": price,
        "cost": cost,
        "order_type": order_type,
        "broker": broker,
        "status": "filled",
        "paper": True,
    }
    trades.append(order)
    _save_trades(trades)
    _save_state(state)
    return order


def get_paper_portfolio() -> dict:
    return _load_state()


def get_paper_trades(limit: int = 50) -> list:
    return _load_trades()[-limit:]


def reset_paper() -> dict:
    from settings import load_settings
    start = load_settings().get("paper_balance_usd", 10000.0)
    state = {"cash": start, "positions": {}}
    _save_state(state)
    _save_trades([])
    return state
