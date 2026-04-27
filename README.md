# PortfolioQuant

> Tracker de patrimoine ETF + Crypto auto-hebergeable, avec bot DCA et rebalancing automatique.

Une web app self-hosted qui agrege en un seul endroit ton portefeuille ETF (via IBKR / Degiro / Alpaca) et tes positions crypto (via MEXC / Binance / Coinbase), automatise ton DCA mensuel, et te donne un dashboard quant facon trading-app.

## Pourquoi ce projet ?

Les solutions existantes sont toutes des SaaS qui voient tes positions, ou des outils techniques sans UI. PortfolioQuant tient sur **ton serveur**, avec **tes cles API**, et te donne quand meme une UI moderne et un bot qui execute pour toi.

## Stack

- **Backend** : Python 3.10 + Flask + APScheduler
- **Frontend** : SPA single-file HTML/CSS/JS (pas de framework, ~13k lignes)
- **Charts** : Plotly + lightweight-charts + SVG natif
- **Securite** : Fernet AES pour les credentials brokers stockes
- **Deploiement** : Railway, Fly.io, Docker, ou serveur Linux quelconque

## Features principales

- **Multi-brokers** : IBKR, Degiro, Alpaca (ETF/actions) + MEXC (crypto)
- **Mode Paper / Live** togglable, kill-switch d'urgence
- **DCA automatique** mensuel avec allocation cible
- **Rebalancing** auto sur drift superieur a un seuil
- **Scanner d'opportunites** sur les marches en temps reel
- **Backtest** et **projection Monte-Carlo** (fan chart)
- **Notifications Telegram** sur trades, erreurs, heartbeats
- **PWA installable** sur mobile

## Installation rapide

### Prerequis

- Python 3.10+
- Un compte chez au moins un broker supporte
- (Optionnel) Un bot Telegram pour les notifications

### Setup local

```bash
# Clone
git clone https://github.com/qmayot44160-hub/etf-portfolio-bot.git
cd etf-portfolio-bot

# Virtual env
python -m venv venv
source venv/bin/activate  # Linux/Mac
# ou : venv\Scripts\activate  # Windows

# Dependances
pip install -r requirements.txt

# Config
cp .env.example .env
# Edite .env pour mettre tes secrets (voir .env.example pour les details)

# Lance
python app.py
# Ouvre http://localhost:5000
```

### Generer les cles requises

```bash
# FERNET_KEY (chiffrement des credentials brokers)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# FLASK_SECRET_KEY (sessions)
python -c "import secrets; print(secrets.token_hex(32))"
```

### Deploiement Railway

1. Fork ce repo
2. Cree un projet Railway pointant sur ton fork
3. Configure les variables d'environnement (voir `.env.example`)
4. Railway detecte automatiquement le `Procfile` et deploie

## Brokers supportes

| Broker | Type | Hebergeable | Statut |
|---|---|---|---|
| **IBKR** (Interactive Brokers) | ETF / Actions | Local uniquement (TWS) | Stable |
| **Degiro** | ETF / Actions | API instable | Beta |
| **Alpaca** | ETF / Actions US | Oui | Stable |
| **MEXC** | Crypto | Oui | Stable |
| **Paper Broker** | Simulation | Oui | Stable |

> **Note IBKR** : Le connecteur actuel utilise `ib_insync` qui necessite TWS / IB Gateway tournant en local. Migration vers IBKR Web API REST en cours pour rendre l'integration hebergeable.

## Configuration

Les variables d'environnement sont documentees dans [`.env.example`](.env.example).

Les preferences utilisateur (allocation cible, montant DCA, broker selectionne, notifications) sont configurees via l'UI une fois l'app lancee.

## Securite

- Tes credentials brokers sont **chiffres Fernet AES** localement (`creds.enc`)
- La cle de chiffrement est dans une variable d'env (`FERNET_KEY`), jamais en dur
- Aucune donnee n'est envoyee a un service tiers : tout reste sur ton serveur
- Auth basique par mot de passe (`APP_PASSWORD`) sur toutes les routes sensibles
- Kill-switch logiciel pour stopper toute execution d'ordres en urgence

## Roadmap

- [ ] Migration IBKR vers Client Portal Web API (deploiement hosted-friendly)
- [ ] Multi-user (auth Supabase + isolation Postgres)
- [ ] Refactor frontend en composants modulaires
- [ ] Export rapport fiscal francais (formulaire 2074)
- [ ] Integration Coinbase + Binance natives
- [ ] Interface tablette / desktop optimisee

## Statut du projet

**Beta active.** L'app tourne en production pour son auteur depuis plusieurs mois.
Self-hosting recommande pour les utilisateurs techniques. Une version SaaS hebergee
est envisagee a moyen terme une fois les questions reglementaires clarifiees.

## Contributions

Les PRs sont bienvenues ! Voir [`CONTRIBUTING.md`](CONTRIBUTING.md) pour les conventions de code.

## Licence

[AGPL-3.0](LICENSE) - tu peux utiliser, modifier et redistribuer librement, mais
toute version modifiee deployee comme service doit aussi etre open-source.

## Soutien

Si ce projet t'est utile, tu peux le soutenir :
- Star le repo
- Ouvrir une issue / PR
- Sponsoriser via [GitHub Sponsors](https://github.com/sponsors/qmayot44160-hub)

## Disclaimer

PortfolioQuant est un outil fourni "en l'etat", sans garantie. L'auteur n'est pas
conseiller en investissement. Toi seul es responsable des ordres executes par ton
instance. Investir comporte des risques de perte en capital.
