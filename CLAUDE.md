# PortfolioQuant - Notes pour Claude

## Style d'écriture

- **Pas de tirets cadratins (em-dash : —)**. Utiliser des tirets simples (-), virgules,
  ou deux-points selon le contexte. Le user les considère comme un tic d'écriture IA.
- Préférer le français direct, pas de tournures emphatiques type "voici", "n'hésitez pas".
- Commentaires de code en français quand c'est explicatif, anglais quand c'est technique.

## Architecture

### Multi-user readiness
L'app est mono-utilisateur aujourd'hui (`APP_USERNAME` / `APP_PASSWORD` côté Railway).
Toute nouvelle feature qui persiste de la donnée user doit utiliser
`data_paths.user_data_path()` plutôt que `data_path()`. Voir docstring du module.

### Frontend
- Tout est dans `templates/index.html` (~13k lignes) - SPA single-file
- CSS inline dans `<style>`, JS inline dans `<script>` à la fin
- Conventions :
  - Variables CSS : `--sf-bg`, `--sf-text`, `--sf-blue`, etc.
  - Mobile breakpoint : `@media (max-width: 720px)`
  - Touch breakpoint : `@media (pointer: coarse)`
  - Vues : `<div id="view-xxx">`, switch via `showView('xxx')`

### Charts
- **Plotly** : lazy-loadé via proxy `window.Plotly`. Reste pour scatter (frontier)
  et heatmap (corrélation) uniquement.
- **lightweight-charts** : ~45 KB, lazy-loadé via `loadLW()`. Wrapper `lwTimeSeriesChart()`.
  Utilisé pour fan chart, scenarios, rolling metrics.
- **SVG natif** : pour donuts (`renderSvgDonut`) et bar charts (`renderSvgBars`).
  Préférer SVG natif quand possible (pas de lib).

### PWA
- `manifest.webmanifest` + `sw.js` servis depuis `/` (scope global)
- Cache strategy : network-first HTML, stale-while-revalidate static, never API
- Bump `VERSION` dans sw.js quand on change static assets

### Focus mode
- N'importe quel élément avec `data-focusable="true"` reçoit un bouton ⤢
- Click → modal centrée + backdrop blur. Le DOM réel est déplacé (pas cloné),
  donc charts/listeners restent vivants. Resize event dispatché au open/close.

### Onboarding wizard
- 4 étapes : welcome → quiz profil → broker → récap
- Auto-trigger si `localStorage.pq_onboarded_v1` absent
- Profil persisté dans `localStorage.pq_profile`

## Brokers

### IBKR
Le connecteur (`brokers/ibkr.py`) utilise `ib_insync` qui se connecte à TWS/IB Gateway
**en local** sur 127.0.0.1:7497 (paper) ou 7496 (live). Donc inutilisable depuis
Railway tel quel. Migration future possible vers IBKR Web API REST.

### MEXC
Crypto. API key dans `creds.enc` chiffré Fernet via `security.py`.

## Règles dures

- **NE JAMAIS** commit sans demander explicitement.
- **NE JAMAIS** push --force sur main.
- Toute feature visible doit être responsive desktop + mobile.
- Préférer SVG natif aux libs externes pour les viz simples.
