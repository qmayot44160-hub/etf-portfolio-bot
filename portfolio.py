"""
Moteur de gestion du portefeuille : calcul des positions, rééquilibrage, DCA.
"""

import json
import os
from datetime import datetime
from config import PORTFOLIO, INITIAL_CAPITAL, DCA_MONTHLY, REBALANCE_THRESHOLD_PCT
from market_data import get_current_prices
from portfolio_allocation import allocate_by_target, compute_rebalance_orders
from data_paths import data_path

PORTFOLIO_FILE = data_path("portfolio_state.json")


def load_state() -> dict:
    """Charge l'état du portefeuille depuis le fichier JSON."""
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE, "r") as f:
            return json.load(f)
    return _init_state()


def save_state(state: dict):
    """Sauvegarde l'état du portefeuille."""
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def _init_state() -> dict:
    """Initialise un portefeuille vide."""
    state = {
        "created_at": datetime.now().isoformat(),
        "cash": INITIAL_CAPITAL,
        "total_invested": 0,
        "positions": {ticker: {"shares": 0, "avg_price": 0} for ticker in PORTFOLIO},
        "transactions": [],
    }
    save_state(state)
    return state


def get_portfolio_value(state: dict = None) -> dict:
    """Calcule la valeur actuelle du portefeuille."""
    if state is None:
        state = load_state()

    prices = get_current_prices()
    positions = []
    total_value = state["cash"]

    for ticker, config in PORTFOLIO.items():
        shares = state["positions"][ticker]["shares"]
        price = prices.get(ticker, 0)
        value = shares * price if price else 0
        total_value += value
        positions.append({
            "ticker": ticker,
            "name": config["name"],
            "category": config["category"],
            "shares": shares,
            "price": price,
            "value": round(value, 2),
            "target_pct": config["target_pct"],
        })

    # Calcul des poids actuels
    for pos in positions:
        pos["current_pct"] = round((pos["value"] / total_value * 100), 2) if total_value > 0 else 0
        pos["drift"] = round(pos["current_pct"] - pos["target_pct"], 2)

    return {
        "total_value": round(total_value, 2),
        "cash": round(state["cash"], 2),
        "positions": positions,
        "last_update": datetime.now().isoformat(),
    }


def calculate_rebalance(state: dict = None) -> list:
    """Calcule les ordres nécessaires pour rééquilibrer le portefeuille."""
    portfolio_value = get_portfolio_value(state)
    return compute_rebalance_orders(
        portfolio_value["positions"],
        portfolio_value["total_value"],
        REBALANCE_THRESHOLD_PCT,
    )


def calculate_dca_allocation(amount: float = None) -> list:
    """Calcule la répartition DCA selon l'allocation cible."""
    if amount is None:
        amount = DCA_MONTHLY
    return allocate_by_target(amount, PORTFOLIO, get_current_prices())


def execute_buy(state: dict, ticker: str, shares: int, price: float) -> dict:
    """Exécute un achat (simulation)."""
    cost = shares * price
    if cost > state["cash"]:
        return {"error": f"Cash insuffisant: {state['cash']:.2f} < {cost:.2f}"}

    pos = state["positions"][ticker]
    total_shares = pos["shares"] + shares
    if total_shares > 0:
        pos["avg_price"] = round(
            (pos["avg_price"] * pos["shares"] + price * shares) / total_shares, 4
        )
    pos["shares"] = total_shares
    state["cash"] -= cost
    state["total_invested"] += cost

    state["transactions"].append({
        "date": datetime.now().isoformat(),
        "ticker": ticker,
        "action": "BUY",
        "shares": shares,
        "price": price,
        "amount": round(cost, 2),
    })

    save_state(state)
    return {"success": True, "shares": shares, "cost": round(cost, 2)}


def execute_dca(state: dict = None, amount: float = None) -> list:
    """Exécute un DCA complet."""
    if state is None:
        state = load_state()
    allocation = calculate_dca_allocation(amount)
    results = []

    for alloc in allocation:
        if alloc["shares_to_buy"] > 0:
            result = execute_buy(
                state, alloc["ticker"], alloc["shares_to_buy"], alloc["price"]
            )
            results.append({**alloc, **result})

    return results
