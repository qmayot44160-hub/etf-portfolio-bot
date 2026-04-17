"""
Moteur de gestion du portefeuille : calcul des positions, rééquilibrage, DCA.
"""

import json
import os
from datetime import datetime
from config import PORTFOLIO, INITIAL_CAPITAL, DCA_MONTHLY, REBALANCE_THRESHOLD_PCT
from market_data import get_current_prices
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
    total = portfolio_value["total_value"]
    orders = []

    for pos in portfolio_value["positions"]:
        drift = abs(pos["drift"])
        if drift >= REBALANCE_THRESHOLD_PCT:
            target_value = total * pos["target_pct"] / 100
            diff_value = target_value - pos["value"]
            if pos["price"] and pos["price"] > 0:
                diff_shares = int(diff_value / pos["price"])
                if diff_shares != 0:
                    orders.append({
                        "ticker": pos["ticker"],
                        "name": pos["name"],
                        "action": "BUY" if diff_shares > 0 else "SELL",
                        "shares": abs(diff_shares),
                        "price": pos["price"],
                        "amount": round(abs(diff_shares) * pos["price"], 2),
                        "drift": pos["drift"],
                    })

    return orders


def calculate_dca_allocation(amount: float = None) -> list:
    """Calcule la répartition DCA selon l'allocation cible."""
    if amount is None:
        amount = DCA_MONTHLY

    prices = get_current_prices()
    allocation = []

    for ticker, config in PORTFOLIO.items():
        alloc_amount = amount * config["target_pct"] / 100
        price = prices.get(ticker, 0)
        if price and price > 0:
            shares = int(alloc_amount / price)
            real_amount = shares * price
            allocation.append({
                "ticker": ticker,
                "name": config["name"],
                "target_amount": round(alloc_amount, 2),
                "price": price,
                "shares_to_buy": shares,
                "real_amount": round(real_amount, 2),
            })

    return allocation


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
