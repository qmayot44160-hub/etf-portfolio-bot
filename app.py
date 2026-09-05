"""
Dashboard web Flask pour le bot ETF.
"""

from datetime import datetime
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, Response
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
from probability_engine import ProbabilityEngine
from multi_horizon import MultiHorizonEngine, DEFAULT_HORIZONS
import prediction_log
from security import (
    get_flask_secret, is_auth_enabled, verify_credentials, is_username_required,
    login_required,
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
# Instances partagees avec la boucle de fond du trader (source unique).
# La boucle peut auto-entrainer les modeles ; l'UI lit le meme etat en memoire.
prob_engine = trader.prob_engine
mh_engine = trader.mh_engine

# ─── Restore des connecteurs persistes ────────────────────
# Au demarrage, on rejoue toutes les connexions sauvegardees (creds chiffrees).
# Si le reseau ou le broker est indispo, le compte reste persiste pour
# une nouvelle tentative au prochain redemarrage.
try:
    from connectors import get_manager as _get_conn_mgr
    _restore_result = _get_conn_mgr().restore_from_storage()
    if _restore_result["restored"] or _restore_result["failed"]:
        print(f"[Startup] Connecteurs restaures : {_restore_result['restored']} OK, "
              f"{_restore_result['failed']} echec(s)")
        for err in _restore_result.get("errors", []):
            print(f"  - {err.get('account_id')}: {err.get('error')}")
except Exception as _e:
    print(f"[Startup] Restore connecteurs ignore : {_e}")


# ── Auth ───────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if not is_auth_enabled():
        return redirect("/")
    if request.method == "POST":
        user = request.form.get("username", "")
        pwd = request.form.get("password", "")
        if verify_credentials(user, pwd):
            session["auth_ok"] = True
            session.permanent = True
            return redirect("/")
        msg = ("Identifiant ou mot de passe incorrect."
               if is_username_required() else "Mot de passe incorrect.")
        return render_template(
            "login.html", error=msg,
            require_username=is_username_required(),
        ), 401
    if session.get("auth_ok"):
        return redirect("/")
    return render_template("login.html", require_username=is_username_required())


@app.route("/logout")
def logout():
    session.clear()
    # Apres deconnexion, on retombe sur la welcome publique : presentation
    # de l'app + CTA "Se connecter" -> /login. Mieux que de tomber direct
    # sur le formulaire de login pour les nouveaux utilisateurs.
    return redirect("/welcome")


@app.route("/welcome")
def welcome():
    """Public landing page (presentation de l'app, CTAs vers /login).
    Whitelistee dans _AUTH_WHITELIST. Les users authentifies peuvent y
    revenir via le bouton 'Retour a l'accueil' du menu utilisateur."""
    return render_template("welcome.html")


@app.route("/api/auth/status")
def api_auth_status():
    return jsonify({
        "auth_enabled": is_auth_enabled(),
        "logged_in": session.get("auth_ok", False) or not is_auth_enabled(),
    })


# ── Auth Guard global ─────────────────────────────────
_AUTH_WHITELIST = {"/login", "/logout", "/welcome", "/api/auth/status", "/static",
                   "/health", "/manifest.webmanifest", "/sw.js", "/favicon.ico"}


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
    # Bloc diagnostic bot (données opérationnelles, non sensibles) : permet de
    # vérifier l'état du trading sans login. Protégé pour ne jamais casser /health.
    try:
        cfg = trader.get_config()
        status["bot"] = {
            "exchange_connected": bool(trader.exchange and getattr(trader.exchange, "connected", False)),
            "exchange_error": getattr(crypto, "last_connect_error", None),
            "trader_running": bool(trader.running),
            "enabled": bool(cfg.get("enabled")),
            "paper_mode": bool(cfg.get("paper_mode", True)),
            "model_trained": bool(trader.prob_engine.is_ready()),
            "active_trades": len(trader.active_trades),
            "predictions": prediction_log.counts(),
        }
    except Exception as e:
        status["bot"] = {"error": str(e)}
    # Retourne 503 si stale (pas vu depuis > 30 min)
    http_code = 200 if status["health"] in ("healthy", "never_seen") else 503
    return jsonify(status), http_code


@app.route("/api/health")
def api_health():
    return jsonify(health.get_status())


@app.errorhandler(404)
def _handle_404(e):
    return render_template('404.html'), 404


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


# ── PWA: manifest et service worker servis depuis la racine ──
# (le SW doit être servi depuis "/" pour avoir un scope global)

@app.route("/manifest.webmanifest")
def pwa_manifest():
    from flask import send_from_directory
    return send_from_directory("static", "manifest.webmanifest",
                               mimetype="application/manifest+json")


@app.route("/sw.js")
def pwa_service_worker():
    from flask import send_from_directory, make_response
    resp = make_response(send_from_directory("static", "sw.js",
                                              mimetype="application/javascript"))
    # Empêche le navigateur de cacher le SW lui-même → MAJ rapides
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.route("/favicon.ico")
def favicon():
    from flask import send_from_directory
    return send_from_directory("static", "quant-icon.svg", mimetype="image/svg+xml")


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
    try:
        notif.notify_kill_switch(reason)
    except Exception:
        pass
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
    """Export complet RGPD - tout ce que l'app sait sur l'utilisateur."""
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


# ── API: Connecteurs (architecture universelle) ─────────
# Coexiste avec /api/brokers ci-dessus. La nouvelle UI utilisera /api/connectors,
# l'ancienne UI continue d'utiliser /api/broker/* tant que la migration n'est pas finie.

@app.route("/api/connectors")
def api_connectors_list():
    """Liste tous les connecteurs disponibles, avec leurs metadata.
    Query optionnel : ?asset_class=crypto pour filtrer.
    """
    from connectors import list_connectors as _list
    asset_class = request.args.get("asset_class")
    return jsonify(_list(asset_class=asset_class))


@app.route("/api/connectors/active")
def api_connectors_active():
    """Liste les comptes actuellement connectes via le manager."""
    from connectors import get_manager
    return jsonify(get_manager().list_active())


@app.route("/api/connectors/connect", methods=["POST"])
def api_connectors_connect():
    """Connecte un connecteur.
    Body : {connector_id: str, credentials: {...}}
    Retourne : {ok, account_id, ...} ou {ok: false, error}.
    """
    from connectors import get_manager
    data = request.get_json() or {}
    connector_id = data.get("connector_id")
    credentials = data.get("credentials", {})
    if not connector_id:
        return jsonify({"ok": False, "error": "connector_id requis"}), 400
    result = get_manager().connect(connector_id, credentials)
    return jsonify(result)


@app.route("/api/connectors/disconnect", methods=["POST"])
def api_connectors_disconnect():
    """Deconnecte un compte. Body : {account_id}."""
    from connectors import get_manager
    data = request.get_json() or {}
    account_id = data.get("account_id")
    if not account_id:
        return jsonify({"ok": False, "error": "account_id requis"}), 400
    return jsonify(get_manager().disconnect(account_id))


@app.route("/api/connectors/positions")
def api_connectors_positions():
    """Toutes les positions agregees de tous les comptes connectes."""
    from connectors import get_manager
    return jsonify(get_manager().all_positions())


@app.route("/api/portfolio/aggregated")
def api_portfolio_aggregated():
    """Vue agregee : total + repartition par classe d'actif + comptes + positions.

    C'est le futur endpoint principal du dashboard une fois la migration finie.
    Pour l'instant ne reflete que les comptes connectes via /api/connectors/connect,
    pas les anciens connectes via /api/broker/connect ou /api/crypto/connect.
    """
    from connectors import get_manager
    return jsonify(get_manager().aggregated_summary())


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


# ── API: Backtest Strategie de Trading ────────────────

@app.route("/api/trading/strategy_backtest")
def api_strategy_backtest():
    """Backtest walk-forward de la TradingStrategy sur donnees historiques."""
    symbol    = request.args.get("symbol", "BTC/USDT")
    periods   = min(request.args.get("periods", 300, type=int), 500)
    timeframe = request.args.get("timeframe", "1h")
    risk_pct  = request.args.get("risk_pct", 1.0, type=float)
    capital   = request.args.get("capital", 10000.0, type=float)
    fee_pct      = request.args.get("fee_pct", 0.10, type=float)
    slippage     = request.args.get("slippage", 0.05, type=float)
    trailing     = request.args.get("trailing", 0, type=int)
    trailing_pct = request.args.get("trailing_pct", 2.0, type=float)
    trailing_act = request.args.get("trailing_act", 1.0, type=float)

    _sync_trader_exchange()
    if not trader.exchange or not trader.exchange.connected:
        return jsonify({"error": "MEXC non connecté. Reconnecte via Connexions → MEXC, ou vérifie ta clé API (permissions Spot, région)."})

    try:
        df = trader._fetch_ohlcv(symbol.upper(), timeframe, limit=periods + 50)
        if len(df) < 150:
            return jsonify({"error": "Pas assez de donnees (minimum 150 candles)"})
        from strategy_backtest import run_strategy_backtest
        result = run_strategy_backtest(
            df, symbol.upper(), capital, risk_pct,
            fee_pct=fee_pct, slippage_pct=slippage,
            trailing_stop=bool(trailing),
            trailing_stop_pct=trailing_pct,
            trailing_activation_pct=trailing_act,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)})


# ── API: Optimisation paramètres (grid search + OOS) ──

@app.route("/api/trading/multi_backtest", methods=["POST"])
@login_required
def api_multi_backtest():
    """
    Lance le backtest sur N symboles en parallele logique (sequentiel + cache).
    Renvoie un classement avec metriques agregees.

    Body JSON : { symbols: [...], timeframe, periods, capital, risk_pct, fee_pct, slippage }
    """
    data       = request.get_json(silent=True) or {}
    symbols    = data.get("symbols") or []
    timeframe  = data.get("timeframe", "1h")
    periods    = min(int(data.get("periods", 300)), 500)
    capital    = float(data.get("capital", 10000.0))
    risk_pct   = float(data.get("risk_pct", 1.0))
    fee_pct    = float(data.get("fee_pct", 0.10))
    slippage   = float(data.get("slippage", 0.05))

    if not symbols:
        return jsonify({"error": "Aucun symbole fourni"})

    _sync_trader_exchange()
    if not trader.exchange or not trader.exchange.connected:
        return jsonify({"error": "MEXC non connecté. Reconnecte via Connexions → MEXC, ou vérifie ta clé API (permissions Spot, région)."})

    from strategy_backtest import run_strategy_backtest
    results = []
    errors = []
    for raw in symbols[:20]:  # cap a 20 pour eviter abus
        sym = raw.upper().strip()
        if "/" not in sym:
            sym = sym + "/USDT"
        try:
            df = trader._fetch_ohlcv(sym, timeframe, limit=periods + 50)
            if len(df) < 150:
                errors.append({"symbol": sym, "error": "Pas assez de donnees"})
                continue
            r = run_strategy_backtest(
                df, sym, capital, risk_pct,
                fee_pct=fee_pct, slippage_pct=slippage,
            )
            if r.get("error"):
                errors.append({"symbol": sym, "error": r["error"]})
                continue
            mc = r.get("monte_carlo") or {}
            results.append({
                "symbol":         sym,
                "return_pct":     r.get("return_pct"),
                "bh_return_pct":  r.get("bh_return_pct"),
                "alpha":          round((r.get("return_pct") or 0) - (r.get("bh_return_pct") or 0), 2),
                "win_rate":       r.get("win_rate"),
                "profit_factor":  r.get("profit_factor"),
                "sharpe":         r.get("sharpe"),
                "max_dd":         r.get("max_drawdown_pct"),
                "n_trades":       r.get("n_trades"),
                "expectancy":     r.get("expectancy"),
                "mc_prob":        mc.get("prob_profitable"),
                "mc_dd_p5":       mc.get("dd_p5"),
                "equity_sparkline": [e["value"] for e in (r.get("equity_curve") or [])][::max(1, len(r.get("equity_curve") or []) // 30)],
            })
        except Exception as e:
            errors.append({"symbol": sym, "error": str(e)})

    # Score composite pour classer : Sharpe x ProfitFactor x WinRate x penalty DD
    def _score(r):
        if not r.get("n_trades") or r["n_trades"] < 3:
            return -999
        sh = (r.get("sharpe") or 0)
        pf = min(r.get("profit_factor") or 0, 10)
        wr = (r.get("win_rate") or 0) / 100
        dd_pen = max(0.1, 1 - abs(r.get("max_dd") or 0) / 100)
        return round(sh * pf * wr * dd_pen, 4)

    for r in results:
        r["score"] = _score(r)
    results.sort(key=lambda r: r["score"], reverse=True)

    return jsonify({
        "results":   results,
        "errors":    errors,
        "n_tested":  len(symbols),
        "timeframe": timeframe,
        "periods":   periods,
    })


# ── API: Moteur probabiliste + apprentissage ───────────

@app.route("/api/probability/status")
@login_required
def api_prob_status():
    """État du modèle probabiliste + calibration live."""
    return jsonify({
        "model": prob_engine.status(),
        "calibration": prediction_log.calibration_report(),
    })


@app.route("/api/probability/train", methods=["POST"])
@login_required
def api_prob_train():
    """
    Entraîne le modèle probabiliste sur l'historique d'un symbole.
    Body JSON : { symbol, timeframe, periods, sl_mult, tp_mult, horizon }
    """
    data      = request.get_json(silent=True) or {}
    symbol    = data.get("symbol", "BTC/USDT")
    timeframe = data.get("timeframe", "1h")
    periods   = min(int(data.get("periods", 1000)), 2000)
    sl_mult   = float(data.get("sl_mult", 1.5))
    tp_mult   = float(data.get("tp_mult", 3.0))
    horizon   = int(data.get("horizon", 24))

    _sync_trader_exchange()
    if not trader.exchange or not trader.exchange.connected:
        return jsonify({"error": "MEXC non connecté. Reconnecte via Connexions → MEXC, ou vérifie ta clé API (permissions Spot, région)."})
    try:
        df = trader._fetch_ohlcv(symbol.upper(), timeframe, limit=periods + 50)
        if len(df) < 200:
            return jsonify({"error": f"Pas assez de donnees ({len(df)} candles, min 200)"})
        result = prob_engine.train_from_history(
            df, symbol.upper(), sl_mult=sl_mult, tp_mult=tp_mult, horizon=horizon,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/probability/predict/<path:symbol>")
@login_required
def api_prob_predict(symbol):
    """Probabilité calibrée pour le dernier candle d'un symbole."""
    timeframe = request.args.get("timeframe", "1h")
    if not prob_engine.is_ready():
        return jsonify({"available": False, "error": "Modèle non entraîné"})
    _sync_trader_exchange()
    if not trader.exchange or not trader.exchange.connected:
        return jsonify({"error": "MEXC non connecté. Reconnecte via Connexions → MEXC, ou vérifie ta clé API (permissions Spot, région)."})
    try:
        sym = symbol.upper()
        if "/" not in sym:
            sym = sym + "/USDT"
        df = trader._fetch_ohlcv(sym, timeframe, limit=250)
        return jsonify(prob_engine.predict(df))
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/probability/calibration")
@login_required
def api_prob_calibration():
    """Rapport de calibration live (Brier, courbe de fiabilité)."""
    return jsonify(prediction_log.calibration_report())


@app.route("/api/probability/predictions")
@login_required
def api_prob_predictions():
    """Dernières prédictions loggées."""
    limit = min(request.args.get("limit", 50, type=int), 200)
    return jsonify({"predictions": prediction_log.recent_predictions(limit)})


@app.route("/api/probability/reconcile", methods=["POST"])
@login_required
def api_prob_reconcile():
    """Réconcilie les prédictions en attente avec les prix actuels."""
    _sync_trader_exchange()
    if not trader.exchange or not trader.exchange.connected:
        return jsonify({"error": "MEXC non connecté. Reconnecte via Connexions → MEXC, ou vérifie ta clé API (permissions Spot, région)."})

    def price_fetcher(sym):
        try:
            return trader.exchange.get_ticker_price(sym)
        except Exception:
            return None

    result = prediction_log.reconcile(price_fetcher)
    return jsonify(result)


@app.route("/api/probability/reset", methods=["POST"])
@login_required
def api_prob_reset():
    """Efface le journal de prédictions (repartir de zéro)."""
    prediction_log.reset_log()
    return jsonify({"status": "ok"})


# ── API: Prévision multi-horizon (IA n°9) ──────────────

@app.route("/api/sentiment/fear_greed")
def api_fear_greed():
    """Index Fear & Greed crypto global (sentiment de marché, IA n6)."""
    from sentiment_feed import get_fear_greed
    return jsonify(get_fear_greed())


@app.route("/api/intermarket")
def api_intermarket():
    """Analyse inter-marchés : corrélations BTC vs macro + biais macro (IA n7)."""
    from intermarket import get_intermarket_analysis
    return jsonify(get_intermarket_analysis())


@app.route("/api/multi_horizon/status")
@login_required
def api_mh_status():
    """État des modèles multi-horizon."""
    return jsonify(mh_engine.status())


@app.route("/api/multi_horizon/train", methods=["POST"])
@login_required
def api_mh_train():
    """
    Entraîne un modèle directionnel par horizon.
    Body JSON : { symbol, timeframe, periods, horizons }
    """
    data      = request.get_json(silent=True) or {}
    symbol    = data.get("symbol", "BTC/USDT")
    timeframe = data.get("timeframe", "1h")
    periods   = min(int(data.get("periods", 1000)), 2000)
    horizons  = data.get("horizons") or DEFAULT_HORIZONS

    _sync_trader_exchange()
    if not trader.exchange or not trader.exchange.connected:
        return jsonify({"error": "MEXC non connecté. Reconnecte via Connexions → MEXC, ou vérifie ta clé API (permissions Spot, région)."})
    try:
        df = trader._fetch_ohlcv(symbol.upper(), timeframe, limit=periods + 50)
        if len(df) < 200:
            return jsonify({"error": f"Pas assez de donnees ({len(df)} candles, min 200)"})
        result = mh_engine.train_from_history(
            df, symbol.upper(), horizons=horizons, timeframe=timeframe,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/multi_horizon/predict/<path:symbol>")
@login_required
def api_mh_predict(symbol):
    """Prévision directionnelle multi-horizon pour un symbole."""
    timeframe = request.args.get("timeframe", "1h")
    if not mh_engine.is_ready():
        return jsonify({"available": False, "error": "Modèles non entraînés"})
    _sync_trader_exchange()
    if not trader.exchange or not trader.exchange.connected:
        return jsonify({"error": "MEXC non connecté. Reconnecte via Connexions → MEXC, ou vérifie ta clé API (permissions Spot, région)."})
    try:
        sym = symbol.upper()
        if "/" not in sym:
            sym = sym + "/USDT"
        df = trader._fetch_ohlcv(sym, timeframe, limit=250)
        return jsonify(mh_engine.predict(df, timeframe))
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/multi_horizon/reset", methods=["POST"])
@login_required
def api_mh_reset():
    """Efface les modèles multi-horizon."""
    mh_engine.reset()
    return jsonify({"status": "ok"})


@app.route("/api/trading/walk_forward")
@login_required
def api_walk_forward():
    """
    Walk-forward optimization : N fenetres glissantes train -> test.
    Test de robustesse temporelle. Un edge qui tient sur 5 periodes
    successives est un edge reel, pas un overfit.
    """
    symbol    = request.args.get("symbol", "BTC/USDT")
    timeframe = request.args.get("timeframe", "1h")
    periods   = min(request.args.get("periods", 1500, type=int), 2000)
    capital   = request.args.get("capital", 10000.0, type=float)
    n_windows = max(3, min(request.args.get("windows", 5, type=int), 10))
    fee_pct   = request.args.get("fee_pct", 0.10, type=float)
    slippage  = request.args.get("slippage", 0.05, type=float)

    _sync_trader_exchange()
    if not trader.exchange or not trader.exchange.connected:
        return jsonify({"error": "MEXC non connecté. Reconnecte via Connexions → MEXC, ou vérifie ta clé API (permissions Spot, région)."})

    try:
        df = trader._fetch_ohlcv(symbol.upper(), timeframe, limit=periods + 50)
        if len(df) < n_windows * 200:
            return jsonify({"error": f"Pas assez de donnees ({len(df)} candles, besoin {n_windows*200})"})
        from optimizer import run_walk_forward
        result = run_walk_forward(
            df, symbol.upper(), capital,
            n_windows=n_windows,
            fee_pct=fee_pct, slippage_pct=slippage,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/trading/optimize")
@login_required
def api_trading_optimize():
    """
    Grid search sl_mult × tp_mult × risk_pct sur 70% des données,
    validation out-of-sample sur les 30% restants.
    Retourne le classement des meilleures combinaisons + heatmap.
    """
    symbol    = request.args.get("symbol", "BTC/USDT")
    timeframe = request.args.get("timeframe", "1h")
    periods   = min(request.args.get("periods", 500, type=int), 1000)
    capital   = request.args.get("capital", 10000.0, type=float)
    fee_pct   = request.args.get("fee_pct", 0.10, type=float)
    slippage  = request.args.get("slippage", 0.05, type=float)

    _sync_trader_exchange()
    if not trader.exchange or not trader.exchange.connected:
        return jsonify({"error": "MEXC non connecté. Reconnecte via Connexions → MEXC, ou vérifie ta clé API (permissions Spot, région)."})

    try:
        df = trader._fetch_ohlcv(symbol.upper(), timeframe, limit=periods + 50)
        if len(df) < 200:
            return jsonify({"error": "Pas assez de données (minimum 200 candles)"})
        from optimizer import run_optimization
        result = run_optimization(
            df, symbol.upper(), capital,
            fee_pct=fee_pct, slippage_pct=slippage,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)})


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


@app.route("/api/trading/history/export")
@login_required
def api_trading_history_export():
    """Exporte le trade history en CSV."""
    import io, csv
    trades = trader.get_trade_history()
    if not trades:
        return Response("Aucun trade", mimetype="text/plain"), 404

    cols = ["symbol", "side", "entry_price", "close_price", "quantity",
            "pnl", "pnl_pct", "stop_loss", "take_profit", "status",
            "opened_at", "closed_at", "regime", "confidence"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(trades)
    csv_str = buf.getvalue()

    filename = f"trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        csv_str,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.route("/api/trading/close", methods=["POST"])
def api_trading_close():
    data = request.get_json()
    return jsonify(trader.close_trade(data.get("symbol", "")))


@app.route("/api/trading/start", methods=["POST"])
def api_trading_start():
    _sync_trader_exchange()
    return jsonify(trader.start())


def _sync_trader_exchange():
    """
    Lie automatiquement le trader au broker crypto connecté.
    Si le broker n'est pas connecté (reconnexion au boot échouée après un redeploy),
    retente une reconnexion depuis la config sauvée avant d'abandonner → auto-guérison
    sans que l'utilisateur ait à re-saisir sa clé.
    """
    if not (crypto.broker and crypto.broker.connected):
        try:
            crypto._load_config()   # relit crypto_config.json et reconnecte
        except Exception:
            pass
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


@app.route("/api/trading/paper_performance")
@login_required
def api_paper_performance():
    """Métriques et equity curve du paper trading."""
    return jsonify(trader.get_paper_performance())


@app.route("/api/trading/paper_reset", methods=["POST"])
@login_required
def api_trader_paper_reset():
    """Remet le capital paper à zéro."""
    trader.reset_paper_trading()
    return jsonify({"status": "ok"})


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
        return jsonify({"error": "MEXC non connecté. Reconnecte via Connexions → MEXC, ou vérifie ta clé API (permissions Spot, région)."}), 400
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
#   Picks du jour - le bot choisit lui-même les meilleurs actifs
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

    # Retry la reconnexion MEXC si le 1er essai au boot a échoué (transitoire).
    if not (crypto.broker and crypto.broker.connected):
        try:
            crypto._load_config()
        except Exception:
            pass

    # Auto-connect trader to MEXC if crypto engine is connected
    if crypto.broker and crypto.broker.connected:
        trader.exchange = crypto.broker
        trader.smart_exec.exchange = crypto.broker
        trader.market_intel.exchange = crypto.broker
        trader.scanner.exchange = crypto.broker
        print("[Startup] Trader auto-linked to MEXC exchange")

    # La boucle se re-synchronise elle-même si l'exchange tombe (voir reconnect_hook).
    trader.reconnect_hook = _sync_trader_exchange

    # Auto-start la boucle si activée, MÊME si l'exchange n'est pas encore prêt :
    # elle tourne à vide et reprend dès que le reconnect_hook rétablit MEXC.
    trader_config = trader.get_config()
    if trader_config.get("enabled") and not trader.running:
        trader.start()
        print("[Startup] Trading loop auto-started")

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
