"""
Dashboard web Flask pour le bot ETF.
"""

from flask import Flask, render_template, jsonify, request
from portfolio import (
    load_state, get_portfolio_value, calculate_rebalance,
    calculate_dca_allocation, execute_dca, _init_state,
)
from backtest import run_backtest
from market_data import get_current_prices, get_etf_info
from config import PORTFOLIO, DCA_MONTHLY

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/portfolio")
def api_portfolio():
    """Retourne l'état actuel du portefeuille."""
    state = load_state()
    value = get_portfolio_value(state)
    return jsonify(value)


@app.route("/api/prices")
def api_prices():
    """Retourne les prix actuels."""
    prices = get_current_prices()
    result = []
    for ticker, price in prices.items():
        result.append({
            "ticker": ticker,
            "name": PORTFOLIO[ticker]["name"],
            "price": price,
            "category": PORTFOLIO[ticker]["category"],
        })
    return jsonify(result)


@app.route("/api/rebalance")
def api_rebalance():
    """Calcule les ordres de rééquilibrage."""
    orders = calculate_rebalance()
    return jsonify(orders)


@app.route("/api/dca")
def api_dca():
    """Calcule l'allocation DCA."""
    amount = request.args.get("amount", DCA_MONTHLY, type=float)
    allocation = calculate_dca_allocation(amount)
    return jsonify(allocation)


@app.route("/api/dca/execute", methods=["POST"])
def api_dca_execute():
    """Exécute un DCA."""
    data = request.get_json() or {}
    amount = data.get("amount", DCA_MONTHLY)
    results = execute_dca(amount=amount)
    return jsonify(results)


@app.route("/api/backtest")
def api_backtest():
    """Lance un backtest."""
    years = request.args.get("years", 5, type=int)
    result = run_backtest(years=years)
    return jsonify(result)


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Réinitialise le portefeuille."""
    state = _init_state()
    return jsonify({"status": "ok", "cash": state["cash"]})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
