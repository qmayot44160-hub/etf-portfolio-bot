"""
Execution intelligente institutionnelle.

- TWAP (Time-Weighted Average Price)
- Order splitting pour minimiser l'impact de marche
- Estimation du slippage
- Smart entry timing (attente de meilleur prix)
- Anti-front-running
- Iceberg orders (ordres fragmentes)
"""

import time
import threading
import numpy as np
from datetime import datetime
from dataclasses import dataclass
from typing import Optional


@dataclass
class ExecutionResult:
    symbol: str
    side: str
    total_quantity: float
    filled_quantity: float
    average_price: float
    target_price: float
    slippage_pct: float
    num_fills: int
    execution_time_seconds: float
    orders: list
    status: str  # "COMPLETE", "PARTIAL", "FAILED"


@dataclass
class SlippageEstimate:
    expected_slippage_pct: float
    impact_score: str  # "LOW", "MEDIUM", "HIGH"
    recommended_strategy: str
    split_into: int
    estimated_cost: float


class SmartExecution:
    """Moteur d'execution intelligent."""

    def __init__(self, exchange=None):
        self.exchange = exchange
        self._active_executions = {}

    # ── Slippage Estimation ──

    def estimate_slippage(
        self,
        symbol: str,
        quantity: float,
        price: float,
        side: str = "BUY",
    ) -> SlippageEstimate:
        """
        Estime le slippage avant d'executer.
        Basé sur le volume moyen et la taille de l'ordre.
        """
        order_value = quantity * price

        # Heuristique basée sur la taille
        if order_value < 100:
            slippage = 0.05
            impact = "LOW"
            splits = 1
        elif order_value < 1000:
            slippage = 0.1
            impact = "LOW"
            splits = 1
        elif order_value < 5000:
            slippage = 0.15
            impact = "MEDIUM"
            splits = 3
        elif order_value < 20000:
            slippage = 0.3
            impact = "MEDIUM"
            splits = 5
        elif order_value < 100000:
            slippage = 0.5
            impact = "HIGH"
            splits = 8
        else:
            slippage = 1.0
            impact = "HIGH"
            splits = 12

        # Try to get actual orderbook depth
        if self.exchange:
            try:
                ob = self.exchange.exchange.fetch_order_book(f"{symbol}/USDT", limit=20)
                book_side = ob["asks"] if side == "BUY" else ob["bids"]

                if book_side:
                    total_liquidity = sum(ask[1] * ask[0] for ask in book_side[:10])
                    fill_ratio = order_value / total_liquidity if total_liquidity > 0 else 1.0

                    # Recalculer le slippage basé sur la liquidité réelle
                    if fill_ratio < 0.01:
                        slippage = 0.02
                        impact = "LOW"
                        splits = 1
                    elif fill_ratio < 0.05:
                        slippage = 0.1
                        impact = "LOW"
                        splits = 2
                    elif fill_ratio < 0.2:
                        slippage = 0.25
                        impact = "MEDIUM"
                        splits = 4
                    else:
                        slippage = fill_ratio * 2
                        impact = "HIGH"
                        splits = max(5, int(fill_ratio * 20))
            except Exception:
                pass

        strategy = "MARKET" if splits == 1 else f"TWAP_{splits}_SPLITS"
        cost = order_value * slippage / 100

        return SlippageEstimate(
            expected_slippage_pct=round(slippage, 3),
            impact_score=impact,
            recommended_strategy=strategy,
            split_into=splits,
            estimated_cost=round(cost, 2),
        )

    # ── TWAP Execution ──

    def execute_twap(
        self,
        symbol: str,
        side: str,
        total_quantity: float,
        num_slices: int = 5,
        interval_seconds: int = 10,
        price_limit: float = None,
    ) -> ExecutionResult:
        """
        Execute un ordre en TWAP (Time-Weighted Average Price).
        Divise l'ordre en tranches et execute sur la duree.
        """
        if not self.exchange:
            return ExecutionResult(
                symbol=symbol, side=side, total_quantity=total_quantity,
                filled_quantity=0, average_price=0, target_price=0,
                slippage_pct=0, num_fills=0, execution_time_seconds=0,
                orders=[], status="FAILED"
            )

        target_price = self._get_price(symbol)
        slice_qty = total_quantity / num_slices
        fills = []
        total_filled = 0
        total_cost = 0
        start_time = time.time()

        for i in range(num_slices):
            # Check price limit
            current_price = self._get_price(symbol)
            if price_limit:
                if side == "BUY" and current_price > price_limit:
                    break  # Prix trop haut
                elif side == "SELL" and current_price < price_limit:
                    break  # Prix trop bas

            remaining = total_quantity - total_filled
            qty = min(slice_qty, remaining)
            if qty <= 0:
                break

            try:
                if side == "BUY":
                    order = self.exchange.buy(symbol, qty)
                else:
                    order = self.exchange.sell(symbol, qty)

                filled_price = current_price  # Approximation
                total_filled += qty
                total_cost += qty * filled_price

                fills.append({
                    "slice": i + 1,
                    "quantity": qty,
                    "price": filled_price,
                    "order_id": order.order_id,
                    "timestamp": datetime.now().isoformat(),
                })
            except Exception as e:
                fills.append({
                    "slice": i + 1,
                    "quantity": qty,
                    "error": str(e),
                    "timestamp": datetime.now().isoformat(),
                })

            # Wait between slices (sauf derniere)
            if i < num_slices - 1 and total_filled < total_quantity:
                time.sleep(interval_seconds)

        elapsed = time.time() - start_time
        avg_price = total_cost / total_filled if total_filled > 0 else 0
        slippage = ((avg_price - target_price) / target_price * 100) if target_price > 0 else 0
        if side == "SELL":
            slippage = -slippage

        status = "COMPLETE" if total_filled >= total_quantity * 0.99 else \
                 "PARTIAL" if total_filled > 0 else "FAILED"

        return ExecutionResult(
            symbol=symbol,
            side=side,
            total_quantity=total_quantity,
            filled_quantity=round(total_filled, 6),
            average_price=round(avg_price, 4),
            target_price=round(target_price, 4),
            slippage_pct=round(slippage, 4),
            num_fills=len([f for f in fills if "error" not in f]),
            execution_time_seconds=round(elapsed, 1),
            orders=fills,
            status=status,
        )

    # ── Smart Order ──

    def smart_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        urgency: str = "normal",  # "low", "normal", "high"
    ) -> ExecutionResult:
        """
        Execute un ordre de maniere intelligente.
        Choisit automatiquement la meilleure strategie.
        """
        price = self._get_price(symbol)
        slippage = self.estimate_slippage(symbol, quantity, price, side)

        if slippage.impact_score == "LOW" or urgency == "high":
            # Petit ordre ou urgent → execute direct
            return self._execute_market(symbol, side, quantity)
        elif slippage.impact_score == "MEDIUM":
            # Ordre moyen → TWAP leger
            return self.execute_twap(
                symbol, side, quantity,
                num_slices=slippage.split_into,
                interval_seconds=5,
            )
        else:
            # Gros ordre → TWAP complet
            return self.execute_twap(
                symbol, side, quantity,
                num_slices=slippage.split_into,
                interval_seconds=15,
            )

    def _execute_market(self, symbol: str, side: str, quantity: float) -> ExecutionResult:
        """Execute un ordre market simple."""
        target_price = self._get_price(symbol)
        start_time = time.time()

        try:
            if side == "BUY":
                order = self.exchange.buy(symbol, quantity)
            else:
                order = self.exchange.sell(symbol, quantity)

            actual_price = self._get_price(symbol)
            slippage = ((actual_price - target_price) / target_price * 100) if target_price > 0 else 0
            if side == "SELL":
                slippage = -slippage

            return ExecutionResult(
                symbol=symbol, side=side, total_quantity=quantity,
                filled_quantity=quantity, average_price=actual_price,
                target_price=target_price, slippage_pct=round(slippage, 4),
                num_fills=1, execution_time_seconds=round(time.time() - start_time, 1),
                orders=[{"order_id": order.order_id, "quantity": quantity, "price": actual_price}],
                status="COMPLETE"
            )
        except Exception as e:
            return ExecutionResult(
                symbol=symbol, side=side, total_quantity=quantity,
                filled_quantity=0, average_price=0, target_price=target_price,
                slippage_pct=0, num_fills=0,
                execution_time_seconds=round(time.time() - start_time, 1),
                orders=[{"error": str(e)}], status="FAILED"
            )

    def _get_price(self, symbol: str) -> float:
        try:
            return self.exchange.get_ticker_price(symbol)
        except Exception:
            return 0

    # ── Optimal Entry ──

    def find_optimal_entry(self, df, side: str = "BUY") -> dict:
        """
        Analyse les niveaux optimaux d'entree.
        Trouve support/resistance + volume profile.
        """
        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        volume = df["volume"].values if "volume" in df.columns else np.ones(len(close))

        if len(close) < 20:
            return {"level": close[-1], "confidence": 50}

        current = close[-1]

        # Support/Resistance par volume profile
        price_range = np.linspace(np.min(low[-50:]), np.max(high[-50:]), 50)
        vol_profile = np.zeros(len(price_range))

        for i in range(-50, 0):
            if len(close) + i <= 0:
                continue
            for j, level in enumerate(price_range):
                if low[i] <= level <= high[i]:
                    vol_profile[j] += volume[i]

        # Points of control (POC) — niveaux de volume max
        poc_indices = np.argsort(vol_profile)[-5:]
        poc_levels = price_range[poc_indices]

        if side == "BUY":
            # Chercher support en dessous
            supports = [l for l in poc_levels if l < current]
            if supports:
                optimal = max(supports)
            else:
                optimal = current * 0.99  # 1% en dessous
        else:
            # Chercher resistance au dessus
            resistances = [l for l in poc_levels if l > current]
            if resistances:
                optimal = min(resistances)
            else:
                optimal = current * 1.01

        distance_pct = abs(optimal - current) / current * 100

        return {
            "current_price": round(current, 4),
            "optimal_entry": round(optimal, 4),
            "distance_pct": round(distance_pct, 2),
            "poc_levels": [round(l, 4) for l in sorted(poc_levels)],
            "recommendation": "LIMIT" if distance_pct > 0.3 else "MARKET",
            "confidence": round(min(80, 50 + vol_profile[poc_indices[-1]] / np.max(vol_profile) * 30), 1),
        }
