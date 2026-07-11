# Roadmap APEX — Arbre de progression

Source de vérité unique pour l'avancement du projet APEX (moteur quantitatif probabiliste).
Construit depuis les 2 docs de vision (`cqcsq.docx` = 9 IAs + support, `vsdvsv.docx` = 11 modules).

**Règle : mettre à jour CE fichier au lieu de relire les docs.** Cocher au fur et à mesure.

Légende : `[x]` fait · `[~]` partiel · `[ ]` pas commencé.
Chaque ligne faite note le fichier concerné. Chaque ligne partielle note ce qui manque.

---

## 1. Collecte de données (Module 1)
- [x] Prix temps réel / OHLC / volumes — `auto_trader._fetch_ohlcv` (ccxt/MEXC)
- [x] Funding rate — `market_intelligence`
- [~] Carnet d'ordres / profondeur — basique ; manque heatmap, iceberg
- [ ] Open Interest
- [ ] Macro-économie / calendrier économique
- [ ] Actualités (News API)
- [ ] Sentiment réseaux sociaux (X, Reddit, Discord, Google Trends)
- [ ] Corrélations inter-actifs (or, pétrole, VIX, DXY)

## 2. Analyses spécialisées (les « IAs »)
### 2.1 Technique — IA n°1 / Module 2 `[~]`
- [x] Indicateurs de base — `technical_analysis.py` (RSI, MACD, EMA, ATR, Bollinger, ADX, CMF, OBV…)
- [x] Multi-timeframes — `auto_trader.analyze_institutional`
- [x] Supports / résistances — `market_intelligence`
- [ ] Ichimoku, SuperTrend, Keltner, Donchian, VWAP, Volume Profile
- [ ] Figures chartistes (patterns) + chandeliers japonais
- [ ] Cycles
### 2.2 Statistique — IA n°2 / Module 4 `[~]`
- [x] Volatilité, corrélations — `quant_models`
- [x] Détection changement de régime — `quant_models`
- [ ] Skewness, Kurtosis, tests de stationnarité, processus stochastiques
### 2.3 Fractale — IA n°3 / Module 3 `[~]`
- [x] Exposant de Hurst — `quant_models`
- [ ] Dimension fractale, analyse multifractale, détection de chaos
### 2.4 Flux d'ordres — IA n°4 `[~]`
- [x] Delta volume, pression acheteuse/vendeuse — `market_intelligence`
- [ ] Iceberg orders, absorption, spoofing (limité par l'API MEXC)
### 2.5 Fondamentale — IA n°5 `[ ]`
- [ ] News, résultats, banques centrales, inflation, taux, géopolitique (score d'impact)
### 2.6 Sentiment — IA n°6 `[~]`
- [x] Fear & Greed Index crypto global (réel) — `sentiment_feed.py`, jauge UI Signaux, champ `market_fng` dans l'analyse
- [ ] X, Reddit, Discord, Telegram, Google Trends, RSS → peur/euphorie/FOMO/capitulation
- [ ] Câbler le F&G comme feature du modèle probabiliste (reporté : ne pas perturber la calibration live en cours)
### 2.7 Inter-marchés — IA n°7 `[ ]`
- [ ] Relations indices/crypto/forex/or/pétrole/obligations/VIX, effets de contagion
### 2.8 Anomalies — IA n°8 `[~]`
- [x] Scan d'opportunités — `market_scanner`
- [ ] Détection manipulation, volumes anormaux, événements rares dédiée
### 2.9 Prévision multi-horizon — IA n°9 `[x]`
- [x] Un modèle par horizon — `multi_horizon.py` (1→48 candles, libellés temporels)

## 3. Machine Learning — Module 5 / « Intelligence Collective » `[~]`
- [x] Modèle probabiliste (régression logistique pur numpy) — `probability_engine.py`
- [ ] Random Forest
- [ ] XGBoost / LightGBM / CatBoost
- [ ] LSTM / Transformers séries temporelles
- [ ] Ensemble learning (vote de plusieurs familles de modèles)
- ⚠️ Note : ces libs sont lourdes → impact build/RAM Railway à évaluer (décision reportée par le user).

## 4. Détection de régime de marché — Module 6 `[x]`
- [x] Haussier/baissier/range/volatil/cassure/retour moyenne — `quant_models`

## 5. Fusion & score de confiance — Module 7 / Fusion des IA `[x]`
- [x] Vote pondéré multi-sources + score global — `auto_trader._combine_signals`
- [x] Probabilité calibrée en sortie — `probability_engine`

## 6. Gestion dynamique du risque — Module 8 `[x]`
- [x] SL/TP optimal (ATR), taille de position, Kelly — `risk_engine.py`
- [x] Espérance mathématique, drawdown attendu — `strategy_backtest`
- [~] Corrélation avec positions ouvertes — à confirmer
- [ ] Effet de levier max recommandé

## 7. Simulation permanente — Module 9 `[x]`
- [x] Backtest walk-forward — `strategy_backtest.py`
- [x] Paper trading complet — `auto_trader` (paper mode)
- [x] Walk-forward multi-fenêtres — `optimizer.run_walk_forward`
- [x] Monte Carlo — `strategy_backtest.monte_carlo_analysis`
- [ ] Stress tests dédiés

## 8. Optimisation — Module 10 `[~]`
- [x] Grid Search + validation OOS — `optimizer.py`
- [~] Recherche aléatoire
- [ ] Optimisation bayésienne
- [ ] Algorithmes génétiques
- [ ] Apprentissage par renforcement

## 9. Décision finale — Module 11 `[x]`
- [x] Gate probabiliste (`min_probability`, off par défaut) — `auto_trader._combine_signals`
- [~] Conditions composées (volume, liquidité, R/R>2, pas d'annonce macro) — proba oui, macro non
- [x] Privilégier l'absence de trade — le gate met en HOLD si sous le seuil

## 10. Apprentissage continu `[x]`
- [x] Log de chaque prédiction — `prediction_log.py`
- [x] Réconciliation prédiction vs résultat réel — boucle `auto_trader`
- [x] Brier score + skill score + courbe de fiabilité — `prediction_log`
- [x] Ré-entraînement auto périodique (7j) — `auto_trader._auto_train_models`

## 11. Explicabilité `[x]`
- [x] Chaque décision justifiée (liste de raisons) — `trading_strategy` / `auto_trader`

---

## Infrastructure (hors docs mais nécessaire)
- [x] Déploiement Railway auto (build mise corrigé) — voir mémoire `railway-deploy`
- [x] Volume persistant + DATA_DIR (survie des modèles ML aux redeploys)
- [x] Mode paper/live avec badge, kill-switch perte journalière
- [x] Mode crypto uniquement (UI Connexions)

---

## Prochaines décisions (par valeur / effort / risque)
1. **Laisser le cœur accumuler de la calibration live** (en cours, phase observation) — effort nul, valeur = savoir si l'edge existe AVANT d'ajouter des couches.
2. ~~Sentiment crypto (Fear & Greed)~~ ✅ FAIT (2026-05-16) — `sentiment_feed.py`, jauge UI.
3. **Inter-marchés** (corrélation BTC/DXY/or via yfinance déjà présent) — IA n°7 faisable sans nouvelle dépendance.
4. **Indicateurs manquants** (Ichimoku, SuperTrend…) — effort faible, complète l'IA n°1.
5. **Ensemble ML léger** (Random Forest via sklearn, plus léger que XGBoost) — grosse valeur ML, dépendance modérée.
6. Optimisation bayésienne, LSTM/Transformers, macro/news — plus lourds, plus tard.
