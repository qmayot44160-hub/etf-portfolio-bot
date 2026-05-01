# Product

## Register

product

## Users

Investisseur particulier autonome, technique, gérant son patrimoine multi-classes (ETF, crypto, immobilier, assurance-vie, livrets) répartis sur plusieurs brokers (IBKR, MEXC, comptes bancaires).

Deux modes d'usage :
- **Check quotidien sur mobile** (1-2 min) : voir allocation, perf du jour, alertes éventuelles.
- **Session approfondie sur desktop** (15-45 min, hebdo ou mensuel) : rebalancer, simuler des scénarios, ajuster les stratégies, lire les analyses.

Mono-user aujourd'hui (`APP_USERNAME` / `APP_PASSWORD`), architecture déjà préparée pour partage multi-user (`data_paths.user_data_path()`).

## Product Purpose

Agréger en une vue unique ce que les apps natives de brokers ne font pas : le portefeuille **complet, cross-broker et cross-classe-d'actifs**. Par-dessus, ajouter la couche quantitative que les outils grand public n'ont pas (frontière efficiente, scénarios Monte Carlo, rolling metrics, rebalancing optimal) pour **décider**, pas seulement contempler.

Succès = l'utilisateur ouvre l'app et sait en moins de 5 secondes ce qui a bougé, où il en est, et ce qu'il devrait faire ensuite (ou rien, si tout va bien).

## Brand Personality

Trois mots : **précis, calme, confiant**.

Voix d'un terminal professionnel qui respecte l'intelligence de l'utilisateur. Pas de gamification, pas de confettis sur un +5%, pas d'emojis dans les notifications. Esthétique de salle de marché tamisée plutôt que d'app fintech grand public. Quand l'app parle, elle parle en français direct, jamais en marketing.

## Anti-references

- **Robinhood, Trade Republic** : trop ludiques, gradients criards, gamification du gain et de la perte.
- **Apps de banques classiques** (BNP, Boursorama, ING) : bureaucratiques, denses sans hiérarchie, datées.
- **Yahoo Finance, Investing.com** : noyés sous les pubs et l'info bruit.
- **Bloomberg Terminal** : trop austère et clavier-centric, pas pensé pour le tactile ni pour l'usage personnel.
- **Crypto-natives type Phantom, Rainbow** : néon, glow, animations partout, esthétique gaming.

## Design Principles

1. **Lisible en une seconde** - l'allocation totale et la perf du jour sont les premières choses qui sautent aux yeux. Tout le reste est à un tap ou un scroll.
2. **Pas de surprise** - chaque écran annonce explicitement ce qu'il fait. Le vocabulaire est cohérent partout (pas de "rendement" ici et "performance" là). Pas de jargon planqué.
3. **Le calme est un feature** - rouge/vert sobres, pas de pulse, pas d'animation sauf quand elle aide à comprendre un changement (transition d'état, pas décoration).
4. **Mobile = vrai mobile** - pas une version dégradée du desktop. L'UX tactile a ses propres patterns (bottom sheets, swipe, tap-targets ≥44px), pas du desktop rétréci.
5. **La donnée sait se taire** - à l'ouverture, on voit les 3 chiffres qui comptent. Le reste se révèle progressivement, à la demande.

## Accessibility & Inclusion

- **WCAG AA** visé : contraste ≥4.5 sur le texte, focus visibles au clavier, navigation clavier complète des vues principales.
- **Reduced motion** respecté : `prefers-reduced-motion` désactive transitions et animations décoratives.
- **Couleur jamais seule** : les +/- sont toujours portés par couleur **et** signe (+/-) ou icône (▲/▼). Daltoniens incluables.
- **Tap targets** ≥44×44 px sur tactile.
- **Lisibilité** : taille de base 16px, échelle ajustable via `--ui-scale`, max-width texte ≤75ch.
