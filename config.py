"""
Configuration du portefeuille ETF — inspiré des recommandations Finary.

Allocation diversifiée mondiale, orientée long terme, low-cost.
"""

# Allocation cible (poids en %)
# Basée sur une approche Finary : coeur mondial + diversification
PORTFOLIO = {
    "IWDA.AS": {  # iShares Core MSCI World (Acc) — coeur du portefeuille
        "name": "MSCI World",
        "target_pct": 50,
        "category": "Actions Monde",
    },
    "ESE.PA": {  # BNP Paribas Easy S&P 500 — surpondération US
        "name": "S&P 500",
        "target_pct": 20,
        "category": "Actions US",
    },
    "PAEEM.PA": {  # Amundi MSCI Emerging Markets — marchés émergents
        "name": "Emerging Markets",
        "target_pct": 10,
        "category": "Actions Emergents",
    },
    "PANX.PA": {  # Amundi Nasdaq-100 — tech/croissance
        "name": "Nasdaq 100",
        "target_pct": 10,
        "category": "Actions Tech",
    },
    "GOVT.PA": {  # Amundi Euro Government Bond — obligations souveraines
        "name": "Obligations Euro",
        "target_pct": 10,
        "category": "Obligations",
    },
}

# Montant total investi (en EUR) — modifiable
INITIAL_CAPITAL = 10000

# Montant DCA mensuel (en EUR)
DCA_MONTHLY = 500

# Seuil de rééquilibrage : on rééquilibre si un ETF dévie de +/- X% de sa cible
REBALANCE_THRESHOLD_PCT = 5

# Devise de référence
CURRENCY = "EUR"

# Historique de backtest (en années)
BACKTEST_YEARS = 5
