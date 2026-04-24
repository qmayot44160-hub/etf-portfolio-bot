"""
Dashboard web Flask pour le bot ETF.
"""

from datetime import datetime
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from bot_engine import BotEngine
from portfolio import (
    load_state, get_portfolio_value, calculate_rebalance,
    calculate_dca_allocation, execute_dca, _init_state,
)
from backtest import run_backtest
from projection import run_projection
from analytics import full_analytics
from market_data import get_current_prices
from brokers import list_brokers
from scheduler import (
    load_scheduler_config, save_scheduler_config,
    setup_scheduled_jobs, get_scheduled_jobs,
)
from config import PORTFOLIO, DCA_MONTHLY, CRYPTO_PORTFOLIO, CRYPTO_DCA_MONTHLY
from crypto_engine import CryptoEngine
from auto_trader import AutoTrader
from security import (
    get_flask_secret, is_auth_enabled, verify_password, login_required,
)
from settings import (
    load_settings, save_settings,
    is_kill_switch_active, trigger_kill_switch, release_kill_switch,
    is_paper_mode, set_paper_mode,
)
import notifications as notif
import paper_broker
import backups
import health
import profile_store

app = Flask(__name__)
app.secret_key = get_flask_secret()
engine = BotEngine()
crypto = CryptoEngine()
trader = AutoTrader()


# ── Auth ───────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if not is_auth_enabled():
        return redirect("/")
    if request.method == "POST":
        pwd = request.form.get("password", "")
        if verify_password(pwd):
            session["auth_ok"] = True
            session.permanent = True
            return redirect("/")
        return render_template("login.html", error="Mot de passe incorrect."), 401
    if session.get("auth_ok"):
        return redirect("/")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/api/auth/status")
def api_auth_status():
    return jsonify({
        "auth_enabled": is_auth_enabled(),
        "logged_in": session.get("auth_ok", False) or not is_auth_enabled(),
    })


# ── Auth Guard global ─────────────────────────────────
_AUTH_WHITELIST = {"/login", "/logout", "/api/auth/status", "/static", "/health"}


@app.before_request
def _global_auth_guard():
    # Heartbeat : à chaque hit, mettre à jour last_seen
    try:
        health.touch()
    except Exception:
        pass

    if not is_auth_enabled():
        return None
    path = request.path or "/"
    # Whitelist (prefix match pour /static)
    for wl in _AUTH_WHITELIST:
        if path == wl or path.startswith(wl + "/"):
            return None
    if session.get("auth_ok"):
        return None
    if path.startswith("/api/"):
        return jsonify({"error": "Authentification requise", "auth_required": True}), 401
    return redirect("/login")


# ── Health endpoint (public, pas d'auth) ──────────────

@app.route("/health")
def health_check():
    """Public endpoint pour UptimeRobot / Railway Healthcheck."""
    status = health.get_status()
    # Retourne 503 si stale (pas vu depuis > 30 min)
    http_code = 200 if status["health"] in ("healthy", "never_seen") else 503
    return jsonify(status), http_code


@app.route("/api/health")
def api_health():
    return jsonify(health.get_status())


@app.errorhandler(500)
def _handle_500(e):
    """Capture les erreurs 500 pour le compteur health + notification Telegram."""
    try:
        health.record_error()
        from notifications import notify_error
        notify_error(f"Erreur 500 sur {request.path}: {e}", source="flask")
    except Exception:
        pass
    return jsonify({"error": "Erreur serveur", "detail": str(e)}), 500


# ── Pages ──────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── API: Settings (kill-switch, paper mode) ───────────

@app.route("/api/settings")
def api_settings():
    """Retourne les settings globaux (kill-switch, paper mode)."""
    s = load_settings()
    s["auth_enabled"] = is_auth_enabled()
    return jsonify(s)


@app.route("/api/settings/paper-mode", methods=["POST"])
def api_settings_paper_mode():
    data = request.get_json() or {}
    enabled = bool(data.get("enabled", True))
    cfg = set_paper_mode(enabled)
    return jsonify({"status": "ok", "paper_mode": cfg["paper_mode"]})


@app.route("/api/kill-switch", methods=["POST"])
def api_kill_switch():
    """Active le kill-switch : stoppe le trader + bloque ordres."""
    data = request.get_json() or {}
    reason = data.get("reason", "Activation manuelle via UI")
    # Stop auto-trader
    try:
        trader.stop()
    except Exception as e:
        print(f"[KillSwitch] trader.stop() error: {e}")
    # Annule les jobs scheduler
    try:
        cfg = load_scheduler_config()
        cfg["dca_enabled"] = False
        cfg["rebalance_enabled"] = False
        save_scheduler_config(cfg)
        setup_scheduled_jobs(cfg)
    except Exception as e:
        print(f"[KillSwitch] scheduler disable error: {e}")
    state = trigger_kill_switch(reason)
    return jsonify({"status": "killed", "settings": state})


@app.route("/api/kill-switch/release", methods=["POST"])
def api_kill_switch_release():
    """Désactive le kill-switch (action humaine)."""
    state = release_kill_switch()
    return jsonify({"status": "released", "settings": state})


# ── API: Notifications (Telegram) ──────────────────────

@app.route("/api/notifications/config", methods=["GET", "POST"])
def api_notifications_config():
    if request.method == "POST":
        data = request.get_json() or {}
        notif.save_config(data)
        return jsonify({"status": "ok", "config": notif.get_config_safe()})
    return jsonify(notif.get_config_safe())


@app.route("/api/notifications/test", methods=["POST"])
def api_notifications_test():
    return jsonify(notif.test_notification())


# ── API: Paper trading ────────────────────────────────

@app.route("/api/paper/portfolio")
def api_paper_portfolio():
    return jsonify(paper_broker.get_paper_portfolio())


@app.route("/api/paper/trades")
def api_paper_trades():
    limit = request.args.get("limit", 50, type=int)
    return jsonify(paper_broker.get_paper_trades(limit))


@app.route("/api/paper/reset", methods=["POST"])
def api_paper_reset():
    return jsonify(paper_broker.reset_paper())


# ── API: Profil utilisateur ────────────────────────────

@app.route("/api/profile", methods=["GET"])
def api_profile_get():
    return jsonify(profile_store.load_profile())


@app.route("/api/profile", methods=["PUT", "POST"])
def api_profile_update():
    data = request.get_json() or {}
    updated = profile_store.save_profile(data)
    profile_store.log_event("profile_update", f"Champs modifiés : {', '.join(data.keys())}")
    return jsonify({"status": "ok", "profile": updated})


@app.route("/api/profile/export")
def api_profile_export():
    """Export complet RGPD — tout ce que l'app sait sur l'utilisateur."""
    from flask import Response
    import json as _json
    payload = profile_store.full_export()
    body = _json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    profile_store.log_event("data_export", f"Export complet ({len(body)} bytes)")
    return Response(
        body,
        mimetype="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="portfolio-quant-export-{datetime_tag()}.json"'
        },
    )


@app.route("/api/profile/reset", methods=["POST"])
def api_profile_reset():
    """Remet le profil aux valeurs par défaut (ne touche pas au portefeuille)."""
    data = request.get_json() or {}
    if data.get("confirm") != "RESET":
        return jsonify({"status": "error", "message": "Confirmation manquante (envoyer {confirm: 'RESET'})"}), 400
    profile = profile_store.reset_profile()
    return jsonify({"status": "ok", "profile": profile})


@app.route("/api/audit-log")
def api_audit_log():
    limit = int(request.args.get("limit", 100))
    return jsonify({"entries": profile_store.get_audit_log(limit)})


def datetime_tag() -> str:
    from datetime import datetime as _dt
    return _dt.utcnow().strftime("%Y%m%d-%H%M%S")


# ── API: Backups ───────────────────────────────────────

@app.route("/api/backups")
def api_backups_list():
    return jsonify(backups.list_backups())


@app.route("/api/backups/create", methods=["POST"])
def api_backups_create():
    data = request.get_json() or {}
    return jsonify(backups.create_backup(label=data.get("label")))


@app.route("/api/backups/restore", methods=["POST"])
def api_backups_restore():
    data = request.get_json() or {}
    backup_id = data.get("backup_id")
    if not backup_id:
        return jsonify({"status": "error", "message": "backup_id manquant"}), 400
    return jsonify(backups.restore_backup(backup_id))


@app.route("/api/backups/delete", methods=["POST"])
def api_backups_delete():
    data = request.get_json() or {}
    backup_id = data.get("backup_id")
    if not backup_id:
        return jsonify({"status": "error", "message": "backup_id manquant"}), 400
    return jsonify(backups.delete_backup(backup_id))


@app.route("/api/backups/prune", methods=["POST"])
def api_backups_prune():
    data = request.get_json() or {}
    keep = data.get("keep_days", 14)
    return jsonify(backups.prune_old_backups(int(keep)))


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


@app.route("/api/portfolio/history")
def api_portfolio_history():
    """
    Historique de la valeur totale du portefeuille (toutes classes d'actifs).
    Query params : range=1d|1w|1m|3m|1y|all, benchmark=SPY|URTH|...
    Retourne {series, source, benchmark, class_breakdown_now, perf_pct}.
    """
    import portfolio_history as ph

    range_key = request.args.get("range", "1m").lower()
    benchmark_sym = request.args.get("benchmark", "").strip().upper()

    pf = engine.get_portfolio()
    try:
        crypto_pf = crypto.get_portfolio()
    except Exception:
        crypto_pf = None

    series, source = ph.get_history_or_reconstruct(pf, crypto_pf, range_key)

    benchmark_series = []
    if benchmark_sym:
        benchmark_series = ph.get_benchmark_series(benchmark_sym, range_key)

    # Perf % sur la période
    perf_pct = None
    if series and len(series) >= 2:
        try:
            v0 = float(series[0].get("total") or 0)
            v1 = float(series[-1].get("total") or 0)
            if v0 > 0:
                perf_pct = round((v1 - v0) / v0 * 100, 2)
        except Exception:
            pass

    return jsonify({
        "range": range_key,
        "source": source,
        "series": series,
        "benchmark_symbol": benchmark_sym or None,
        "benchmark_series": benchmark_series,
        "perf_pct": perf_pct,
        "current": ph.classify_portfolio(pf, crypto_pf),
    })


@app.route("/api/themes")
def api_themes_list():
    """Liste des thèmes Discover avec perf moyenne. Cache 1h."""
    import themes as th
    force = request.args.get("force", "").lower() in ("1", "true")
    return jsonify({"themes": th.get_themes_with_perf(force_refresh=force)})


@app.route("/api/themes/<theme_id>")
def api_theme_detail(theme_id):
    import themes as th
    detail = th.get_theme_detail(theme_id)
    if not detail:
        return jsonify({"error": "theme_not_found"}), 404
    return jsonify(detail)


@app.route("/api/watchlist", methods=["GET"])
def api_watchlist_list():
    import watchlist as wl
    return jsonify({"items": wl.list_items()})


@app.route("/api/watchlist/add", methods=["POST"])
def api_watchlist_add():
    import watchlist as wl
    data = request.get_json() or {}
    return jsonify(wl.add(
        data.get("ticker", ""),
        name=data.get("name", ""),
        asset_class=data.get("asset_class", "etf"),
        category=data.get("category", ""),
    ))


@app.route("/api/watchlist/<ticker>", methods=["DELETE"])
def api_watchlist_remove(ticker):
    import watchlist as wl
    return jsonify(wl.remove(ticker))


@app.route("/api/watchlist/<ticker>/alert", methods=["POST"])
def api_watchlist_set_alert(ticker):
    import watchlist as wl
    data = request.get_json() or {}
    above = data.get("above")
    below = data.get("below")
    # Permet null/0 pour effacer explicitement
    return jsonify(wl.set_alert(ticker,
        above=above if above is not None else None,
        below=below if below is not None else None,
    ))


@app.route("/api/watchlist/<ticker>/alert", methods=["DELETE"])
def api_watchlist_clear_alert(ticker):
    import watchlist as wl
    return jsonify(wl.clear_alert(ticker))


@app.route("/api/watchlist/check/<ticker>")
def api_watchlist_check(ticker):
    import watchlist as wl
    return jsonify({"watched": wl.is_watched(ticker)})


@app.route("/api/portfolio/snapshot", methods=["POST"])
def api_portfolio_snapshot():
    """Force l'enregistrement d'un snapshot maintenant."""
    import portfolio_history as ph
    pf = engine.get_portfolio()
    try:
        crypto_pf = crypto.get_portfolio()
    except Exception:
        crypto_pf = None
    snap = ph.snapshot(pf, crypto_pf)
    return jsonify({"ok": True, "snapshot": snap})


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
    # Un rebalance modifie les positions -> invalide le cache prix pour la prochaine lecture
    try:
        from market_data import invalidate_price_cache
        invalidate_price_cache()
    except Exception:
        pass
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
    """Lance un backtest avancé."""
    years = request.args.get("years", 5, type=int)
    capital = request.args.get("capital", None, type=float)
    dca = request.args.get("dca", None, type=float)
    rebalance = request.args.get("rebalance", "true").lower() == "true"
    result = run_backtest(years=years, capital=capital, dca=dca, rebalance=rebalance)
    return jsonify(result)


# ── API: Analytics ─────────────────────────────────────

@app.route("/api/analytics")
def api_analytics():
    """Analyse quantitative avancée : optimisation, corrélations, stress tests, régime."""
    years = request.args.get("years", 5, type=int)
    return jsonify(full_analytics(years=years))


# ── API: Projection ────────────────────────────────────

@app.route("/api/projection")
def api_projection():
    """Projection Monte Carlo des gains futurs."""
    years = request.args.get("years", 10, type=int)
    capital = request.args.get("capital", None, type=float)
    dca = request.args.get("dca", None, type=float)
    n_sim = request.args.get("n_simulations", 2000, type=int)
    inflation = request.args.get("inflation", 2.0, type=float)
    target = request.args.get("target", None, type=float)
    history = request.args.get("history_years", 5, type=int)
    result = run_projection(
        capital=capital, dca=dca, years=years,
        n_simulations=n_sim, inflation_pct=inflation,
        target_amount=target, history_years=history,
    )
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


@app.route("/api/trading/scan", methods=["POST"])
def api_trading_scan():
    """Lance un scan complet du marche."""
    _sync_trader_exchange()
    trader.scanner.exchange = trader.exchange
    return jsonify(trader.scanner.full_scan())


@app.route("/api/trading/scan/results")
def api_trading_scan_results():
    """Resultats du dernier scan."""
    return jsonify(trader.scanner.get_cached_results())


@app.route("/api/trading/scan/movers")
def api_trading_scan_movers():
    """Top movers rapide."""
    _sync_trader_exchange()
    trader.scanner.exchange = trader.exchange
    return jsonify(trader.scanner.get_top_movers())


@app.route("/api/trading/scan/config", methods=["GET", "POST"])
def api_trading_scan_config():
    if request.method == "POST":
        data = request.get_json()
        trader.scanner.save_config(data)
        return jsonify({"status": "ok"})
    return jsonify(trader.scanner.config)


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


# ── API: Smart Picks ────────────────────────────────────
#   Picks du jour — le bot choisit lui-même les meilleurs actifs
#   en combinant son scanner ETF autonome + le scanner crypto existant.

@app.route("/api/smart-picks")
def api_smart_picks():
    """Top picks unifiés (ETF + crypto), triés par score."""
    from etf_scanner import get_etf_scanner

    limit = int(request.args.get("limit", 5))
    asset_class = request.args.get("class", "all")  # "all" | "etf" | "crypto"

    etf_picks = []
    crypto_picks = []

    # ETF
    if asset_class in ("all", "etf"):
        scanner = get_etf_scanner()
        cached = scanner.get_cached_results(limit=limit * 2)
        # Si cache vide ou > 1h → lance un scan async (ne bloque pas la requête)
        needs_refresh = False
        if not cached.get("last_scan"):
            needs_refresh = True
        else:
            try:
                last = datetime.fromisoformat(cached["last_scan"])
                if (datetime.now() - last).total_seconds() > 3600:
                    needs_refresh = True
            except Exception:
                needs_refresh = True
        if needs_refresh and not cached.get("is_scanning"):
            scanner.scan_async(force=True)
        etf_picks = cached.get("results", [])[:limit]

    # Crypto (du scanner existant)
    if asset_class in ("all", "crypto"):
        try:
            if trader.scanner.exchange and trader.scanner.exchange.connected:
                cached = trader.scanner.get_cached_results()
                raw = cached.get("results", [])[:limit]
                # Normalize shape to match ETF picks for frontend
                for r in raw:
                    crypto_picks.append({
                        "ticker": r.get("symbol", "").replace("/USDT", ""),
                        "name": r.get("symbol", "").replace("/USDT", ""),
                        "category": r.get("category", "CRYPTO"),
                        "price": r.get("price", 0),
                        "score": r.get("score", 0),
                        "direction": "BULL" if r.get("direction") == "LONG" else "BEAR",
                        "change_1d": r.get("price_change_24h", 0),
                        "change_7d": r.get("price_change_7d", 0),
                        "change_30d": r.get("price_change_7d", 0),
                        "volume_surge": 1 + (r.get("volume_change_pct", 0) / 100),
                        "reasons": r.get("reasons", []),
                        "sparkline": [],  # crypto scanner n'en fournit pas
                        "asset_class": "crypto",
                    })
        except Exception as e:
            print(f"[smart-picks] crypto fetch error: {e}")

    # Ajoute asset_class à l'ETF
    for p in etf_picks:
        p["asset_class"] = "etf"

    # Retour unifié
    return jsonify({
        "etf": etf_picks,
        "crypto": crypto_picks,
        "last_updated": datetime.now().isoformat(timespec="seconds"),
    })


@app.route("/api/smart-picks/refresh", methods=["POST"])
def api_smart_picks_refresh():
    """Force un re-scan en arrière-plan."""
    from etf_scanner import get_etf_scanner
    scanner = get_etf_scanner()
    r = scanner.scan_async(force=True)
    return jsonify(r)


@app.route("/api/smart-picks/history")
def api_smart_picks_history():
    """Historique des picks passés + performance réalisée depuis chaque pick."""
    from etf_scanner import get_etf_scanner
    limit = int(request.args.get("limit", 20))
    scanner = get_etf_scanner()
    return jsonify(scanner.get_history_with_perf(limit=limit))


@app.route("/api/smart-picks/heatmap")
def api_smart_picks_heatmap():
    """
    Renvoie la liste complète (~40) des ETF scannés, classés par catégorie,
    pour une visualisation heatmap à la Finviz.
    """
    from etf_scanner import get_etf_scanner, ETF_UNIVERSE
    scanner = get_etf_scanner()
    cache = scanner.get_cached_results(limit=200)
    results = cache.get("results", [])

    # Si pas de cache : envoie la liste (sans scores) pour afficher des tuiles grises
    if not results:
        return jsonify({
            "tiles": [
                {
                    "ticker": e["ticker"], "name": e["name"], "category": e["category"],
                    "score": None, "direction": "NEUTRAL",
                    "change_1d": None, "change_7d": None, "change_30d": None,
                    "price": None, "sparkline": [],
                } for e in ETF_UNIVERSE
            ],
            "last_scan": None,
            "is_scanning": cache.get("is_scanning", False),
        })

    # Tri par catégorie puis par score décroissant
    tiles = sorted(results, key=lambda r: (r.get("category", ""), -(r.get("score") or 0)))
    slim = [{
        "ticker": r.get("ticker"),
        "name": r.get("name"),
        "category": r.get("category"),
        "score": r.get("score"),
        "direction": r.get("direction", "NEUTRAL"),
        "change_1d": r.get("change_1d"),
        "change_7d": r.get("change_7d"),
        "change_30d": r.get("change_30d"),
        "price": r.get("price"),
        "sparkline": r.get("sparkline", []),
        "volatility_30d": r.get("volatility_30d"),
        "sharpe_30d": r.get("sharpe_30d"),
        "trend_strength": r.get("trend_strength"),
        "volume_surge": r.get("volume_surge"),
        "reasons": r.get("reasons", []),
        "asset_class": "etf",
    } for r in tiles]
    return jsonify({
        "tiles": slim,
        "last_scan": cache.get("last_scan"),
        "is_scanning": cache.get("is_scanning", False),
    })


# ── API: Reset ─────────────────────────────────────────

@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Réinitialise le portefeuille simulé."""
    state = _init_state()
    return jsonify({"status": "ok", "cash": state["cash"]})


# ── Startup ────────────────────────────────────────────

def init_app():
    """Initialisation au démarrage."""
    # Enregistre le démarrage + notifie Telegram
    try:
        health.record_startup()
    except Exception as e:
        print(f"[Startup] health.record_startup error: {e}")

    # Force setup des jobs scheduler (inclut daily_backup + daily_heartbeat désormais)
    config = load_scheduler_config()
    setup_scheduled_jobs(config)

    # Auto-connect trader to MEXC if crypto engine is connected
    if crypto.broker and crypto.broker.connected:
        trader.exchange = crypto.broker
        trader.smart_exec.exchange = crypto.broker
        trader.market_intel.exchange = crypto.broker
        trader.scanner.exchange = crypto.broker
        print("[Startup] Trader auto-linked to MEXC exchange")

        # Auto-start trading loop if enabled in config
        trader_config = trader.get_config()
        if trader_config.get("enabled") and not trader.running:
            trader.start()
            print("[Startup] Trading loop auto-started (LIVE mode)")

    # Kick off an initial ETF scan in the background (non-blocking).
    # Si un cache existe déjà et est récent (<1h), scan_async court-circuitera.
    try:
        from etf_scanner import get_etf_scanner
        get_etf_scanner().scan_async(force=False)
        print("[Startup] ETF smart-picks scanner triggered")
    except Exception as e:
        print(f"[Startup] ETF scanner init error: {e}")

    # Watchlist : démarre le checker d'alertes en arrière-plan
    try:
        import watchlist as _wl
        _wl.start_background_checker(interval_sec=300)
    except Exception as e:
        print(f"[Startup] watchlist checker error: {e}")

    # Snapshot quotidien de la valeur portefeuille (toutes classes)
    try:
        import portfolio_history as _ph
        import threading as _threading
        def _initial_snapshot():
            try:
                pf = engine.get_portfolio()
                try:
                    crypto_pf = crypto.get_portfolio()
                except Exception:
                    crypto_pf = None
                _ph.snapshot(pf, crypto_pf)
                print("[Startup] Portfolio snapshot recorded")
            except Exception as e:
                print(f"[Startup] snapshot error: {e}")
        _threading.Thread(target=_initial_snapshot, daemon=True).start()
    except Exception as e:
        print(f"[Startup] portfolio_history init error: {e}")


init_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
