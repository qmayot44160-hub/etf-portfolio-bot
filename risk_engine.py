"""
Moteur de risque institutionnel.

- Value at Risk (VaR) historique + parametrique
- Expected Shortfall (CVaR)
- Position sizing (Kelly + fractional Kelly)
- Matrice de correlation
- Portfolio heat / exposure limits
- Circuit breakers
- Drawdown control dynamique
- Risk-adjusted sizing
"""

import json
import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Optional


from data_paths import data_path

RISK_CONFIG_FILE = data_path("risk_config.json")
RISK_LOG_FILE = data_path("risk_log.json")


@dataclass
class RiskMetrics:
    """Metriques de risque du portefeuille."""
    total_exposure: float  # % du capital expose
    total_exposure_usd: float
    long_exposure: float
    short_exposure: float
    net_exposure: float
    var_95: float  # Value at Risk 95%
    var_99: float  # Value at Risk 99%
    cvar_95: float  # Expected Shortfall 95%
    max_portfolio_loss: float  # Pire scenario
    current_drawdown: float
    max_drawdown: float
    daily_pnl: float
    weekly_pnl: float
    correlation_risk: float  # 0-100, risque de concentration
    position_count: int
    largest_position_pct: float
    portfolio_heat: float  # % du capital en risque total
    risk_score: int  # 0-100 (0=safe, 100=danger)
    circuit_breaker_triggered: bool
    warnings: list


class RiskEngine:
    """Moteur de gestion du risque institutionnel."""

    def __init__(self):
        self.config = self._load_config()
        self.risk_log = self._load_risk_log()
        self.peak_equity = 0
        self.daily_pnl_start = 0

    def _load_config(self) -> dict:
        if os.path.exists(RISK_CONFIG_FILE):
            with open(RISK_CONFIG_FILE, "r") as f:
                return json.load(f)
        return {
            # Limites de risque
            "max_portfolio_risk_pct": 6.0,  # Max % capital en risque
            "max_single_position_pct": 15.0,  # Max % par position
            "max_correlated_exposure_pct": 30.0,  # Max exposition correlée
            "max_total_exposure_pct": 100.0,  # Max exposition totale
            "max_drawdown_pct": 15.0,  # Max drawdown avant circuit breaker
            "max_daily_loss_pct": 3.0,  # Max perte journaliere
            "max_weekly_loss_pct": 7.0,  # Max perte hebdo

            # Position sizing
            "default_risk_per_trade_pct": 1.0,  # Risque par trade
            "kelly_fraction": 0.25,  # Fraction de Kelly (conservative)
            "max_position_size_pct": 10.0,  # Taille max position

            # Circuit breakers
            "circuit_breaker_enabled": True,
            "cooldown_after_losses": 3,  # Pause apres X pertes consecutives
            "cooldown_duration_minutes": 60,

            # VaR params
            "var_confidence": 0.95,
            "var_lookback_days": 30,

            # Correlation
            "correlation_threshold": 0.7,  # Seuil pour considerer correlé
        }

    def save_config(self, config: dict):
        self.config = config
        with open(RISK_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)

    def _load_risk_log(self) -> list:
        if os.path.exists(RISK_LOG_FILE):
            with open(RISK_LOG_FILE, "r") as f:
                return json.load(f)
        return []

    def _save_risk_log(self):
        with open(RISK_LOG_FILE, "w") as f:
            json.dump(self.risk_log[-1000:], f, indent=2)

    def log_event(self, event_type: str, message: str, data: dict = None):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
            "message": message,
            "data": data or {},
        }
        self.risk_log.append(entry)
        self._save_risk_log()

    # ── VaR & CVaR ──

    def calculate_var(self, returns: np.ndarray, confidence: float = 0.95) -> tuple:
        """
        Calcule VaR et CVaR (Expected Shortfall).
        Returns (VaR, CVaR) en % de perte.
        """
        if len(returns) < 5:
            return 0, 0

        # VaR historique
        sorted_returns = np.sort(returns)
        index = int((1 - confidence) * len(sorted_returns))
        var = -sorted_returns[index] if index < len(sorted_returns) else 0

        # CVaR (moyenne des pertes au-dela du VaR)
        tail = sorted_returns[:index + 1]
        cvar = -np.mean(tail) if len(tail) > 0 else var

        return round(var * 100, 3), round(cvar * 100, 3)

    def calculate_parametric_var(self, returns: np.ndarray, confidence: float = 0.95) -> float:
        """VaR parametrique (hypothese de normalite)."""
        if len(returns) < 5:
            return 0
        from scipy.stats import norm
        mu = np.mean(returns)
        sigma = np.std(returns)
        var = -(mu + norm.ppf(1 - confidence) * sigma)
        return round(var * 100, 3)

    # ── Correlation Matrix ──

    def calculate_correlation_matrix(self, price_data: dict) -> dict:
        """
        Calcule la matrice de correlation entre les actifs.
        price_data: {symbol: [prices]}
        """
        if len(price_data) < 2:
            return {"matrix": {}, "high_correlations": [], "risk_score": 0}

        # Convertir en returns
        returns_dict = {}
        min_len = min(len(prices) for prices in price_data.values())
        min_len = max(min_len, 2)

        for symbol, prices in price_data.items():
            if len(prices) >= min_len:
                p = np.array(prices[-min_len:], dtype=float)
                r = np.diff(np.log(p))
                returns_dict[symbol] = r

        if len(returns_dict) < 2:
            return {"matrix": {}, "high_correlations": [], "risk_score": 0}

        symbols = list(returns_dict.keys())
        returns_matrix = np.array([returns_dict[s] for s in symbols])
        corr = np.corrcoef(returns_matrix)

        # Build result
        matrix = {}
        high_corr = []
        for i, s1 in enumerate(symbols):
            matrix[s1] = {}
            for j, s2 in enumerate(symbols):
                c = round(corr[i][j], 3) if not np.isnan(corr[i][j]) else 0
                matrix[s1][s2] = c
                if i < j and abs(c) > self.config["correlation_threshold"]:
                    high_corr.append({
                        "pair": f"{s1}/{s2}",
                        "correlation": c,
                        "risk": "HIGH" if abs(c) > 0.85 else "MEDIUM",
                    })

        # Risk score basé sur la correlation moyenne
        if len(symbols) > 1:
            avg_corr = np.mean(np.abs(corr[np.triu_indices_from(corr, k=1)]))
            risk_score = min(avg_corr * 100, 100)
        else:
            risk_score = 0

        return {
            "matrix": matrix,
            "symbols": symbols,
            "high_correlations": high_corr,
            "average_correlation": round(avg_corr if len(symbols) > 1 else 0, 3),
            "risk_score": round(risk_score, 1),
        }

    # ── Position Sizing ──

    def calculate_position_size(
        self,
        capital: float,
        entry_price: float,
        stop_loss: float,
        signal_confidence: float = 50,
        kelly_fraction: float = None,
        volatility: float = None,
    ) -> dict:
        """
        Calcul institutionnel de la taille de position.
        Combine : risk-based, Kelly, volatility-adjusted.
        """
        if entry_price <= 0 or stop_loss <= 0:
            return {"quantity": 0, "error": "Prix invalide"}

        risk_per_unit = abs(entry_price - stop_loss)
        if risk_per_unit <= 0:
            return {"quantity": 0, "error": "SL = prix d'entree"}

        # 1. Risk-based sizing (classique)
        risk_pct = self.config["default_risk_per_trade_pct"]
        risk_amount = capital * risk_pct / 100
        qty_risk = risk_amount / risk_per_unit

        # 2. Kelly sizing
        kf = kelly_fraction or self.config["kelly_fraction"]
        win_rate = max(0.35, min(0.65, signal_confidence / 100))
        rr_ratio = 2.0  # Assume 2:1 RR
        kelly_full = (win_rate * rr_ratio - (1 - win_rate)) / rr_ratio
        kelly_adj = max(0, kelly_full * kf)
        qty_kelly = (capital * kelly_adj) / entry_price

        # 3. Volatility-adjusted (si vol disponible)
        qty_vol = qty_risk
        if volatility and volatility > 0:
            vol_scalar = 20 / max(volatility, 5)  # Normalise a 20% vol
            vol_scalar = max(0.3, min(2.0, vol_scalar))
            qty_vol = qty_risk * vol_scalar

        # Prendre le minimum des trois methodes (conservateur)
        quantity = min(qty_risk, qty_kelly, qty_vol)

        # 4. Cap a max_position_size
        max_qty = (capital * self.config["max_position_size_pct"] / 100) / entry_price
        quantity = min(quantity, max_qty)

        # 5. Cap a max_single_position
        max_single = (capital * self.config["max_single_position_pct"] / 100) / entry_price
        quantity = min(quantity, max_single)

        cost = quantity * entry_price
        position_pct = cost / capital * 100 if capital > 0 else 0
        risk_pct_actual = (risk_per_unit * quantity) / capital * 100 if capital > 0 else 0

        return {
            "quantity": round(quantity, 6),
            "cost": round(cost, 2),
            "position_pct": round(position_pct, 2),
            "risk_amount": round(risk_per_unit * quantity, 2),
            "risk_pct": round(risk_pct_actual, 2),
            "method": "min(risk, kelly, vol_adj)",
            "details": {
                "qty_risk_based": round(qty_risk, 6),
                "qty_kelly": round(qty_kelly, 6),
                "qty_vol_adjusted": round(qty_vol, 6),
                "kelly_fraction": round(kelly_adj, 4),
                "vol_scalar": round(vol_scalar if volatility else 1.0, 2),
            }
        }

    # ── Portfolio Risk Assessment ──

    def assess_portfolio_risk(
        self,
        active_trades: list,
        capital: float,
        trade_history: list = None,
    ) -> RiskMetrics:
        """
        Evaluation complete du risque du portefeuille.
        """
        trade_history = trade_history or []
        warnings = []

        # Expositions
        long_exp = sum(
            t.get("entry_price", 0) * t.get("quantity", 0)
            for t in active_trades if t.get("side") == "BUY"
        )
        short_exp = sum(
            t.get("entry_price", 0) * t.get("quantity", 0)
            for t in active_trades if t.get("side") == "SELL"
        )
        total_exp = long_exp + short_exp
        net_exp = long_exp - short_exp

        total_exp_pct = total_exp / capital * 100 if capital > 0 else 0
        long_pct = long_exp / capital * 100 if capital > 0 else 0
        short_pct = short_exp / capital * 100 if capital > 0 else 0
        net_pct = net_exp / capital * 100 if capital > 0 else 0

        # Portfolio heat (total risk)
        portfolio_heat = 0
        for t in active_trades:
            entry = t.get("entry_price", 0)
            sl = t.get("stop_loss", 0)
            qty = t.get("quantity", 0)
            risk = abs(entry - sl) * qty
            portfolio_heat += risk
        heat_pct = portfolio_heat / capital * 100 if capital > 0 else 0

        # Position concentration
        position_sizes = []
        for t in active_trades:
            size = t.get("entry_price", 0) * t.get("quantity", 0)
            position_sizes.append(size)
        largest_pos_pct = max(position_sizes) / capital * 100 if position_sizes and capital > 0 else 0

        # PnL metrics
        pnls = [t.get("pnl", 0) for t in trade_history]
        daily_pnl = sum(
            t.get("pnl", 0) for t in trade_history
            if t.get("closed_at", "") > (datetime.now() - timedelta(days=1)).isoformat()
        )
        weekly_pnl = sum(
            t.get("pnl", 0) for t in trade_history
            if t.get("closed_at", "") > (datetime.now() - timedelta(days=7)).isoformat()
        )

        # Drawdown
        if pnls:
            cumulative = np.cumsum(pnls)
            peak = np.maximum.accumulate(cumulative)
            drawdowns = cumulative - peak
            current_dd = drawdowns[-1] if len(drawdowns) > 0 else 0
            max_dd = np.min(drawdowns) if len(drawdowns) > 0 else 0
            current_dd_pct = current_dd / capital * 100 if capital > 0 else 0
            max_dd_pct = max_dd / capital * 100 if capital > 0 else 0
        else:
            current_dd_pct = 0
            max_dd_pct = 0

        # VaR from trade history
        if len(pnls) > 5:
            pnl_returns = np.array(pnls) / capital if capital > 0 else np.zeros(len(pnls))
            var_95, cvar_95 = self.calculate_var(pnl_returns, 0.95)
            var_99, _ = self.calculate_var(pnl_returns, 0.99)
        else:
            var_95 = heat_pct * 0.5  # Estimation
            var_99 = heat_pct * 0.7
            cvar_95 = heat_pct * 0.6

        max_loss = portfolio_heat  # Si tous les SL touchent

        # Risk warnings
        if total_exp_pct > self.config["max_total_exposure_pct"]:
            warnings.append(f"Exposition totale ({total_exp_pct:.1f}%) depasse la limite ({self.config['max_total_exposure_pct']}%)")
        if heat_pct > self.config["max_portfolio_risk_pct"]:
            warnings.append(f"Portfolio heat ({heat_pct:.1f}%) depasse la limite ({self.config['max_portfolio_risk_pct']}%)")
        if largest_pos_pct > self.config["max_single_position_pct"]:
            warnings.append(f"Position la plus large ({largest_pos_pct:.1f}%) depasse la limite ({self.config['max_single_position_pct']}%)")
        if abs(current_dd_pct) > self.config["max_drawdown_pct"]:
            warnings.append(f"DRAWDOWN CRITIQUE: {current_dd_pct:.1f}%")
        daily_loss_pct = abs(daily_pnl) / capital * 100 if capital > 0 and daily_pnl < 0 else 0
        if daily_loss_pct > self.config["max_daily_loss_pct"]:
            warnings.append(f"Perte journaliere ({daily_loss_pct:.1f}%) depasse la limite ({self.config['max_daily_loss_pct']}%)")
        weekly_loss_pct = abs(weekly_pnl) / capital * 100 if capital > 0 and weekly_pnl < 0 else 0
        if weekly_loss_pct > self.config["max_weekly_loss_pct"]:
            warnings.append(f"Perte hebdo ({weekly_loss_pct:.1f}%) depasse la limite ({self.config['max_weekly_loss_pct']}%)")

        # Circuit breaker check
        circuit_breaker = False
        if self.config["circuit_breaker_enabled"]:
            if abs(current_dd_pct) > self.config["max_drawdown_pct"]:
                circuit_breaker = True
            if daily_loss_pct > self.config["max_daily_loss_pct"]:
                circuit_breaker = True
            # Check consecutive losses
            recent = trade_history[-self.config["cooldown_after_losses"]:]
            if len(recent) >= self.config["cooldown_after_losses"]:
                if all(t.get("pnl", 0) < 0 for t in recent):
                    circuit_breaker = True
                    warnings.append(f"{self.config['cooldown_after_losses']} pertes consecutives — cooldown active")

        # Risk score composite (0=safe, 100=danger)
        risk_score = 0
        risk_score += min(total_exp_pct / self.config["max_total_exposure_pct"] * 25, 25)
        risk_score += min(heat_pct / self.config["max_portfolio_risk_pct"] * 25, 25)
        risk_score += min(abs(current_dd_pct) / self.config["max_drawdown_pct"] * 25, 25)
        risk_score += min(largest_pos_pct / self.config["max_single_position_pct"] * 25, 25)
        risk_score = min(100, risk_score)
        if circuit_breaker:
            risk_score = 100

        return RiskMetrics(
            total_exposure=round(total_exp_pct, 2),
            total_exposure_usd=round(total_exp, 2),
            long_exposure=round(long_pct, 2),
            short_exposure=round(short_pct, 2),
            net_exposure=round(net_pct, 2),
            var_95=round(var_95, 2),
            var_99=round(var_99, 2),
            cvar_95=round(cvar_95, 2),
            max_portfolio_loss=round(max_loss, 2),
            current_drawdown=round(current_dd_pct, 2),
            max_drawdown=round(max_dd_pct, 2),
            daily_pnl=round(daily_pnl, 2),
            weekly_pnl=round(weekly_pnl, 2),
            correlation_risk=0,  # Calculé séparément
            position_count=len(active_trades),
            largest_position_pct=round(largest_pos_pct, 2),
            portfolio_heat=round(heat_pct, 2),
            risk_score=round(risk_score),
            circuit_breaker_triggered=circuit_breaker,
            warnings=warnings,
        )

    # ── Trade Authorization ──

    def authorize_trade(
        self,
        active_trades: list,
        capital: float,
        new_trade: dict,
        trade_history: list = None,
    ) -> dict:
        """
        Autorise ou refuse un nouveau trade.
        Comme un risk manager qui valide chaque operation.
        """
        trade_history = trade_history or []
        risk = self.assess_portfolio_risk(active_trades, capital, trade_history)

        reasons = []

        # Circuit breaker
        if risk.circuit_breaker_triggered:
            self.log_event("BLOCKED", f"Circuit breaker — trade {new_trade.get('symbol')} refuse", {"risk_score": risk.risk_score})
            return {
                "authorized": False,
                "reason": "Circuit breaker active - trading suspendu",
                "risk_score": risk.risk_score,
                "warnings": risk.warnings,
            }

        # Check exposure limits
        new_exposure = new_trade.get("entry_price", 0) * new_trade.get("quantity", 0)
        new_total_exp = risk.total_exposure + (new_exposure / capital * 100 if capital > 0 else 0)

        if new_total_exp > self.config["max_total_exposure_pct"]:
            reasons.append(f"Exposition totale ({new_total_exp:.1f}%) depasserait la limite")

        # Check position size
        new_pos_pct = new_exposure / capital * 100 if capital > 0 else 0
        if new_pos_pct > self.config["max_single_position_pct"]:
            reasons.append(f"Taille position ({new_pos_pct:.1f}%) depasse la limite")

        # Check portfolio heat
        new_risk = abs(new_trade.get("entry_price", 0) - new_trade.get("stop_loss", 0)) * new_trade.get("quantity", 0)
        new_heat = risk.portfolio_heat + (new_risk / capital * 100 if capital > 0 else 0)
        if new_heat > self.config["max_portfolio_risk_pct"]:
            reasons.append(f"Portfolio heat ({new_heat:.1f}%) depasserait la limite")

        # Check daily loss
        if risk.daily_pnl < 0:
            daily_loss_pct = abs(risk.daily_pnl) / capital * 100 if capital > 0 else 0
            if daily_loss_pct > self.config["max_daily_loss_pct"] * 0.8:
                reasons.append(f"Perte journaliere proche de la limite ({daily_loss_pct:.1f}%)")

        # Risk score gate
        if risk.risk_score > 80:
            reasons.append(f"Risk score trop eleve ({risk.risk_score}/100)")

        authorized = len(reasons) == 0

        if not authorized:
            self.log_event("BLOCKED", f"Trade {new_trade.get('symbol')} refuse: {'; '.join(reasons)}", {
                "risk_score": risk.risk_score,
                "trade": new_trade,
            })
        else:
            self.log_event("AUTHORIZED", f"Trade {new_trade.get('symbol')} autorise", {
                "risk_score": risk.risk_score,
                "new_heat": round(new_heat, 2),
            })

        return {
            "authorized": authorized,
            "reason": "; ".join(reasons) if reasons else "Autorise",
            "risk_score": risk.risk_score,
            "new_portfolio_heat": round(new_heat, 2),
            "new_total_exposure": round(new_total_exp, 2),
            "warnings": risk.warnings,
            "position_size_approved": new_trade.get("quantity", 0) if authorized else 0,
        }
