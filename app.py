"""
Dashboard web Flask pour le bot ETF.
"""

from flask import Flask, render_template, jsonify, request
from bot_engine import BotEngine
from portfolio import (
    load_state, get_portfolio_value, calculate_rebalance,
    calculate_dca_allocation, execute_dca, _init_state,
)
from backtest import run_backtest
from market_data import get_current_prices
from brokers import list_brokers
from scheduler import (
    load_scheduler_config, save_scheduler_config,
    setup_scheduled_jobs, get_scheduled_jobs,
)
from config import PORTFOLIO, DCA_MONTHLY, CRYPTO_PORTFOLIO, CRYPTO_DCA_MONTHLY
from crypto_engine import CryptoEngine
from auto_trader import AutoTrader

app = Flask(__name__)
engine = BotEngine()
crypto = CryptoEngine()
trader = AutoTrader()


# ── Pages ──────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── API: Bot Status ────────────────────────────────────

@app.route("/api/status")
def api_status():
    """Statut global du bot (broker, mode, scheduler)."""
    status = engine.get_status()
    status["scheduler"] = {
        "config": load_scheduler_config(),
        "jobs": get_scheduled_jobs(),
    }
    return jsonify(status)


# ── API: Broker ────────────────────────────────────────

@app.route("/api/brokers")
def api_brokers():
    """Liste les brokers disponibles."""
    return jsonify(list_brokers())


@app.route("/api/broker/connect", methods=["POST"])
def api_broker_connect():
    """Connecte un broker."""
    data = request.get_json()
    broker_id = data.get("broker_id")
    credentials = data.get("credentials", {})
    result = engine.connect_broker(broker_id, credentials)
    return jsonify(result)


@app.route("/api/broker/disconnect", methods=["POST"])
def api_broker_disconnect():
    """Déconnecte le broker."""
    result = engine.disconnect_broker()
    return jsonify(result)


@app.route("/api/broker/account")
def api_broker_account():
    """Info du compte broker."""
    if not engine.broker or not engine.broker.connected:
        return jsonify({"error": "Aucun broker connecté"}), 400
    try:
        from dataclasses import asdict
        account = engine.broker.get_account()
        return jsonify(asdict(account))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── API: Portfolio ─────────────────────────────────────

@app.route("/api/portfolio")
def api_portfolio():
    """Retourne le portefeuille (broker réel ou simulation)."""
    result = engine.get_portfolio()
    return jsonify(result)


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


# ── API: Rebalance ─────────────────────────────────────

@app.route("/api/rebalance")
def api_rebalance():
    """Calcule les ordres de rééquilibrage."""
    if engine.broker and engine.broker.connected:
        orders = engine.execute_rebalance()
    else:
        orders = calculate_rebalance()
    return jsonify(orders)


@app.route("/api/rebalance/execute", methods=["POST"])
def api_rebalance_execute():
    """Exécute le rééquilibrage via le broker."""
    results = engine.execute_rebalance()
    return jsonify(results)


# ── API: DCA ───────────────────────────────────────────

@app.route("/api/dca")
def api_dca():
    """Calcule l'allocation DCA."""
    amount = request.args.get("amount", DCA_MONTHLY, type=float)
    allocation = calculate_dca_allocation(amount)
    return jsonify(allocation)


@app.route("/api/dca/execute", methods=["POST"])
def api_dca_execute():
    """Exécute un DCA (broker ou simulation)."""
    data = request.get_json() or {}
    amount = data.get("amount", DCA_MONTHLY)
    results = engine.execute_dca(amount=amount)
    return jsonify(results)


# ── API: Backtest ──────────────────────────────────────

@app.route("/api/backtest")
def api_backtest():
    """Lance un backtest."""
    years = request.args.get("years", 5, type=int)
    result = run_backtest(years=years)
    return jsonify(result)


# ── API: Scheduler ─────────────────────────────────────

@app.route("/api/scheduler")
def api_scheduler():
    """Config et état du scheduler."""
    config = load_scheduler_config()
    jobs = get_scheduled_jobs()
    return jsonify({"config": config, "jobs": jobs})


@app.route("/api/scheduler/update", methods=["POST"])
def api_scheduler_update():
    """Met à jour la config du scheduler."""
    data = request.get_json()
    config = load_scheduler_config()
    config.update(data)
    setup_scheduled_jobs(config)
    return jsonify({"status": "ok", "config": config})


# ── API: Crypto ────────────────────────────────────────

@app.route("/api/crypto/status")
def api_crypto_status():
    return jsonify(crypto.get_status())


@app.route("/api/crypto/connect", methods=["POST"])
def api_crypto_connect():
    data = request.get_json()
    result = crypto.connect(data.get("broker_id", "mexc"), data.get("credentials", {}))
    return jsonify(result)


@app.route("/api/crypto/disconnect", methods=["POST"])
def api_crypto_disconnect():
    return jsonify(crypto.disconnect())


@app.route("/api/crypto/portfolio")
def api_crypto_portfolio():
    return jsonify(crypto.get_portfolio())


@app.route("/api/crypto/dca")
def api_crypto_dca():
    amount = request.args.get("amount", CRYPTO_DCA_MONTHLY, type=float)
    return jsonify(crypto.calculate_dca(amount))


@app.route("/api/crypto/dca/execute", methods=["POST"])
def api_crypto_dca_execute():
    data = request.get_json() or {}
    amount = data.get("amount", CRYPTO_DCA_MONTHLY)
    return jsonify(crypto.execute_dca(amount))


@app.route("/api/crypto/rebalance")
def api_crypto_rebalance():
    return jsonify(crypto.calculate_rebalance())


# ── API: Trading ───────────────────────────────────────

@app.route("/api/trading/status")
def api_trading_status():
    return jsonify(trader.get_status())


@app.route("/api/trading/config", methods=["GET", "POST"])
def api_trading_config():
    if request.method == "POST":
        data = request.get_json()
        trader.save_config(data)
        return jsonify({"status": "ok"})
    return jsonify(trader.get_config())


@app.route("/api/trading/analyze/<symbol>")
def api_trading_analyze(symbol):
    _sync_trader_exchange()
    tf = request.args.get("timeframe", "1h")
    return jsonify(trader.analyze_symbol(symbol.upper(), tf))


@app.route("/api/trading/analyze_all")
def api_trading_analyze_all():
    _sync_trader_exchange()
    return jsonify(trader.analyze_all())


@app.route("/api/trading/execute", methods=["POST"])
def api_trading_execute():
    data = request.get_json()
    return jsonify(trader.execute_signal(data))


@app.route("/api/trading/active")
def api_trading_active():
    return jsonify(trader.get_active_trades())


@app.route("/api/trading/history")
def api_trading_history():
    return jsonify(trader.get_trade_history())


@app.route("/api/trading/close", methods=["POST"])
def api_trading_close():
    data = request.get_json()
    return jsonify(trader.close_trade(data.get("symbol", "")))


@app.route("/api/trading/start", methods=["POST"])
def api_trading_start():
    _sync_trader_exchange()
    return jsonify(trader.start())


def _sync_trader_exchange():
    """Lie automatiquement le trader au broker crypto connecté."""
    if crypto.broker and crypto.broker.connected:
        trader.exchange = crypto.broker


@app.route("/api/trading/stop", methods=["POST"])
def api_trading_stop():
    return jsonify(trader.stop())


@app.route("/api/trading/check_sltp")
def api_trading_check_sltp():
    return jsonify(trader.check_stop_loss_take_profit())


@app.route("/api/trading/performance")
def api_trading_performance():
    return jsonify(trader.get_performance())


@app.route("/api/trading/risk")
def api_trading_risk():
    _sync_trader_exchange()
    return jsonify(trader.get_risk_dashboard())


@app.route("/api/trading/risk/config", methods=["GET", "POST"])
def api_trading_risk_config():
    if request.method == "POST":
        data = request.get_json()
        trader.risk_engine.save_config(data)
        return jsonify({"status": "ok"})
    return jsonify(trader.risk_engine.config)


@app.route("/api/trading/risk/log")
def api_trading_risk_log():
    return jsonify(trader.risk_engine.risk_log[-50:])


@app.route("/api/trading/intel/<symbol>")
def api_trading_intel(symbol):
    _sync_trader_exchange()
    if not trader.exchange or not trader.exchange.connected:
        return jsonify({"error": "Exchange non connecte"}), 400
    try:
        import pandas as pd
        df = trader._fetch_ohlcv(symbol.upper(), "1h", 200)
        intel = trader.market_intel
        intel.exchange = trader.exchange
        result = intel.full_analysis(df, symbol.upper())
        from dataclasses import asdict
        return jsonify(asdict(result))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trading/symbols", methods=["GET", "POST"])
def api_trading_symbols():
    """Gestion des symboles de trading."""
    if request.method == "POST":
        data = request.get_json()
        action = data.get("action")  # "add" or "remove"
        symbol = data.get("symbol", "").upper()
        if not symbol:
            return jsonify({"error": "Symbole manquant"}), 400

        config = trader.get_config()
        symbols = config.get("symbols", [])

        if action == "add" and symbol not in symbols:
            symbols.append(symbol)
        elif action == "remove" and symbol in symbols:
            symbols.remove(symbol)
        else:
            return jsonify({"status": "no_change", "symbols": symbols})

        config["symbols"] = symbols
        trader.save_config(config)
        return jsonify({"status": "ok", "symbols": symbols})

    config = trader.get_config()
    return jsonify({"symbols": config.get("symbols", [])})


# ── API: Reset ─────────────────────────────────────────

@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Réinitialise le portefeuille simulé."""
    state = _init_state()
    return jsonify({"status": "ok", "cash": state["cash"]})


# ── Startup ────────────────────────────────────────────

def init_app():
    """Initialisation au démarrage."""
    config = load_scheduler_config()
    if config.get("dca_enabled") or config.get("rebalance_enabled"):
        setup_scheduled_jobs(config)


init_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
