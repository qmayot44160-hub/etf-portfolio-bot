"""
Utilitaires d'allocation partagés entre ``portfolio``, ``bot_engine`` et ``crypto_engine``.

Avant ce module, la même math d'allocation était recopiée 3 fois. Un bug
ici devait être corrigé en 3 endroits. Désormais tout passe par :

- :func:`allocate_by_target` — répartit un montant sur des cibles pondérées
  (utilisé par DCA, ETF et crypto).
- :func:`compute_rebalance_orders` — transforme des positions + drift en
  ordres BUY/SELL (utilisé par le rebalance simulé et réel).

Les formes de retour sont stables (pas d'évolutions silencieuses), ce qui
permet à la couche broker (bot_engine) et la couche API (app.py) de les
consommer sans adapter leur contrat.
"""

from typing import Iterable, Mapping


def allocate_by_target(amount: float, targets: Mapping[str, dict], prices: Mapping[str, float]) -> list[dict]:
    """Répartit ``amount`` sur ``targets`` selon la pondération ``target_pct``.

    ``targets`` est un dict ``{ticker: {"name": ..., "target_pct": ..., "category": ...}}``
    (compatible avec la structure ``config.PORTFOLIO``). Les tickers sans prix
    valide sont silencieusement skippés.

    Forme de retour stable (consommée par l'API DCA) :

    .. code-block:: python

        [{
            "ticker": str, "name": str, "category": str,
            "target_pct": float, "target_amount": float,
            "price": float, "shares_to_buy": int, "real_amount": float,
        }, ...]
    """
    out: list[dict] = []
    for ticker, cfg in targets.items():
        alloc_amount = amount * cfg["target_pct"] / 100
        price = prices.get(ticker, 0) or 0
        if price <= 0:
            continue
        shares = int(alloc_amount / price)
        out.append({
            "ticker": ticker,
            "name": cfg.get("name", ticker),
            "category": cfg.get("category", "Autre"),
            "target_pct": cfg["target_pct"],
            "target_amount": round(alloc_amount, 2),
            "price": price,
            "shares_to_buy": shares,
            "real_amount": round(shares * price, 2),
        })
    return out


def compute_rebalance_orders(
    positions: Iterable[dict],
    total_value: float,
    threshold_pct: float,
) -> list[dict]:
    """Transforme une liste de positions en ordres BUY/SELL basés sur le drift.

    Chaque entrée de ``positions`` doit exposer au minimum :
    ``ticker``, ``name``, ``target_pct``, ``value``, ``price``, ``drift``.
    (Les deux portefeuilles — simulé et broker — fournissent déjà ce schéma.)

    Forme de retour stable :

    .. code-block:: python

        [{
            "ticker": str, "name": str,
            "action": "BUY" | "SELL",
            "shares": int, "price": float, "amount": float,
            "drift": float,
        }, ...]
    """
    orders: list[dict] = []
    for pos in positions:
        drift = abs(pos.get("drift", 0) or 0)
        if drift < threshold_pct:
            continue
        target_value = total_value * pos["target_pct"] / 100
        diff_value = target_value - pos["value"]
        price = pos.get("price") or 0
        if price <= 0:
            continue
        diff_shares = int(diff_value / price)
        if diff_shares == 0:
            continue
        orders.append({
            "ticker": pos["ticker"],
            "name": pos.get("name", pos["ticker"]),
            "action": "BUY" if diff_shares > 0 else "SELL",
            "shares": abs(diff_shares),
            "price": price,
            "amount": round(abs(diff_shares) * price, 2),
            "drift": pos.get("drift", 0),
        })
    return orders
