---
name: PortfolioQuant
description: Dashboard quant pour gérer un patrimoine multi-classes cross-broker.
colors:
  bg-canvas: "#000000"
  bg-surface: "#101010"
  bg-card: "#1d1d1f"
  bg-card-hover: "#2a2a2c"
  border-hairline: "rgba(255,255,255,0.08)"
  text-primary: "#f5f5f7"
  text-secondary: "#86868b"
  text-muted: "#6e6e73"
  accent-blue: "#2997ff"
  accent-blue-hover: "#0077ed"
  signal-up: "#30d158"
  signal-down: "#ff453a"
  signal-warn: "#ff9f0a"
  signal-rare-purple: "#bf5af2"
  signal-rare-teal: "#64d2ff"
typography:
  display:
    fontFamily: "Inter, -apple-system, 'SF Pro Display', sans-serif"
    fontSize: "clamp(2rem, 5vw, 3rem)"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Inter, -apple-system, 'SF Pro Display', sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Inter, -apple-system, 'SF Pro Text', sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: "Inter, sans-serif"
    fontSize: "0.66rem"
    fontWeight: 600
    letterSpacing: "0.08em"
  numeric:
    fontFamily: "Inter, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    fontFeature: "'tnum' 1, 'ss01' 1"
rounded:
  chip: "8px"
  card: "22px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "22px"
  xl: "32px"
  xxl: "60px"
components:
  card:
    backgroundColor: "{colors.bg-card}"
    rounded: "{rounded.card}"
    padding: "22px"
  card-hover:
    backgroundColor: "{colors.bg-card-hover}"
    rounded: "{rounded.card}"
  chip-neutral:
    backgroundColor: "rgba(255,255,255,0.04)"
    textColor: "#d6d6d9"
    rounded: "{rounded.chip}"
    padding: "0 10px"
    height: "26px"
  button-primary:
    backgroundColor: "{colors.accent-blue}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.chip}"
    padding: "10px 20px"
  button-primary-hover:
    backgroundColor: "{colors.accent-blue-hover}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.chip}"
---

# Design System: PortfolioQuant

## 1. Overview

**Creative North Star: "La salle de marché tamisée"**

PortfolioQuant ressemble à un terminal pro vu à 23h, lumière éteinte. Le canvas est noir absolu (`#000000`), les cartes flottent à peine au-dessus en gris très sombre (`#1d1d1f`), les chiffres sont blanc cassé tirant vers le chaud (`#f5f5f7`). Aucune décoration ne vient distraire de ce que l'utilisateur est venu chercher : ses chiffres. La typographie est une seule famille, Inter, déclinée en quatre rôles, calée sur les chiffres tabulaires (`tnum`) pour que les colonnes de prix s'alignent au pixel.

L'inspiration visible vient d'Apple - palette `--sf-*`, blur 22px sur la nav, radius 22px sur les cartes - mais le ton est volontairement plus austère qu'apple.com. PortfolioQuant n'est pas un produit grand public à vendre : c'est un outil personnel qu'on consulte vite, parfois cinq fois par jour. Donc pas de hero gigantesque, pas de copy poétique, pas d'illustrations. Juste de la donnée, hiérarchisée, qu'on peut lire en une seconde.

Ce que le système refuse explicitement : les gradients criards de Robinhood, la densité bureaucratique des apps de banque, le bruit publicitaire de Yahoo Finance, l'austérité clavier-only de Bloomberg. La promesse est entre les deux : pro, mais habitable. Riche, mais lisible.

**Key Characteristics:**
- Canvas noir absolu, surfaces flottées en gris quasi-noir.
- Une seule famille typographique (Inter), chiffres tabulaires partout où il y a des nombres.
- Accent bleu rare et fonctionnel, jamais décoratif.
- Vert/rouge sobres, toujours doublés d'un signe ou d'une icône.
- Coins arrondis généreux (22px) qui adoucissent la densité.
- Aurora et grain en fond - décoration ambiante - désactivables d'un toggle.

## 2. Colors

Palette monochromatique tirée vers le neutre froid, ponctuée de cinq couleurs fonctionnelles. Aucune couleur n'est purement décorative : chacune porte un sens.

### Primary
- **Quant Blue** (`#2997ff`) : action principale, lien actif, focus state. Réservé aux CTAs et aux bordures de focus. Présence cible ≤8% de la surface.

### Signals (rôle sémantique, jamais décoratif)
- **Up Green** (`#30d158`) : performances positives, statuts "live", succès. Toujours accompagné d'un signe `+` ou icône `▲`.
- **Down Red** (`#ff453a`) : performances négatives, kill switch hover, erreurs. Toujours accompagné d'un signe `-` ou icône `▼`.
- **Warn Orange** (`#ff9f0a`) : mode paper, alertes non bloquantes, états transitoires.

### Neutral
- **Canvas Black** (`#000000`) : fond global, derrière tout.
- **Surface Charcoal** (`#101010`) : strates intermédiaires (drawers, sheets fermés).
- **Card Onyx** (`#1d1d1f`) : surfaces de contenu (cartes, panels).
- **Card Onyx Hover** (`#2a2a2c`) : cartes survolées / pressées.
- **Hairline** (`rgba(255,255,255,0.08)`) : séparateurs, bordures de cartes. Jamais opaque.
- **Text Primary** (`#f5f5f7`) : titres, chiffres-clés. Jamais `#fff` pur.
- **Text Secondary** (`#86868b`) : labels, légendes, métadonnées.
- **Text Muted** (`#6e6e73`) : placeholders, états désactivés.

### Rare
- **Plot Purple** (`#bf5af2`) et **Plot Teal** (`#64d2ff`) : exclusivement pour différencier des séries dans les charts (ex. portfolio vs benchmark). Jamais dans le chrome de l'UI.

### Named Rules

**The One Voice Rule.** L'accent bleu apparaît au maximum sur 8% de la surface visible d'un écran. Sa rareté fait son autorité : si tout est bleu, plus rien ne l'est.

**The Sign-First Rule.** Toute valeur signée (perf, P&L, variation) porte d'abord son signe (`+` / `-`) ou une icône directionnelle (`▲` / `▼`). La couleur est secondaire. Daltoniens et copies-collés en monochrome doivent rester lisibles.

**The Never-Pure Rule.** Pas de `#000` ni de `#fff` dans le contenu. Le canvas est `#000` par décision technique (économie OLED, ambiance), mais tout ce qui est lu est un noir ou un blanc tinté.

## 3. Typography

**Display Font:** Inter (chargé en `wght 300-800`)
**Body Font:** Inter
**Label/Mono Font:** Inter avec `font-feature-settings: 'tnum'` pour les nombres tabulaires.

**Character:** Une seule famille, modulée par poids et taille. Inter a été choisie pour sa lisibilité écran à toute taille, ses chiffres tabulaires natifs, et sa neutralité sérieuse. Pas de second font de "personality" - la personnalité de PortfolioQuant est dans la donnée, pas dans la typographie.

### Hierarchy
- **Display** (700, `clamp(2rem, 5vw, 3rem)`, 1.1, `letter-spacing: -0.02em`) : titres de page (Dashboard, Allocation), une fois par écran.
- **Title** (600, 1.25rem, 1.3) : titres de cartes et sections.
- **Numeric** (600, 1.5rem, `tnum`) : chiffres-clés (valeur portfolio, perf du jour). Toujours en chiffres tabulaires.
- **Body** (400, 1rem, 1.5) : texte courant, descriptions. Max 65-75ch.
- **Label** (600, 0.66rem, `letter-spacing: 0.08em`, UPPERCASE) : étiquettes de chips, micro-headers de tableaux, statuts.

### Named Rules

**The Tabular Numbers Rule.** Tout nombre affiché dans un contexte de comparaison (table, liste, KPI) utilise `font-feature-settings: 'tnum' 1`. Les colonnes s'alignent au pixel ou elles ne s'affichent pas.

**The One Display Per View Rule.** Une seule taille Display par écran. Si vous en mettez deux, l'une des deux n'est pas Display - rétrogradez-la en Title.

## 4. Elevation

Système flat-by-default, profondeur par tonal layering plutôt que par ombres. Les surfaces se distinguent par leur teinte (`#000` → `#101010` → `#1d1d1f` → `#2a2a2c`), pas par des ombres portées. Une exception : les chips et le logo ont des reliefs internes très subtils (inset shadows ≤ 1px).

### Shadow Vocabulary (rare, fonctionnel)
- **Hover lift** (`0 6px 18px rgba(255,69,58,0.22)` sur kill-chip, équivalent bleu sur boutons) : feedback de survol uniquement, jamais au repos.
- **Inset gloss** (`inset 0 1px 0 rgba(255,255,255,0.08)`) : sur le logo header, signature de qualité Apple.

### Named Rules

**The Flat-By-Default Rule.** Aucune surface ne porte d'ombre au repos. Les ombres apparaissent uniquement comme réponse à un état (`:hover`, `:focus`, `:active`).

**The Tonal Stack Rule.** La profondeur se lit par les quatre teintes de gris (`#000` / `#101010` / `#1d1d1f` / `#2a2a2c`), dans cet ordre du fond vers la surface. Pas de cinquième teinte.

## 5. Components

### Buttons
- **Shape:** Rounded rectangle (8px sur les chips/CTAs petits, 22px sur les CTAs majeurs).
- **Primary:** Fond `#2997ff`, texte `#f5f5f7`, padding `10px 20px`. Hover `#0077ed`, transform translateY(-1px), shadow `0 6px 18px rgba(41,151,255,0.22)`.
- **Ghost / Secondary:** Fond `rgba(255,255,255,0.04)`, bordure 1px `rgba(255,255,255,0.09)`, texte `#d6d6d9`. Hover : bordure tend vers la couleur sémantique (rouge sur kill, bleu sur action).
- **Focus:** Outline 2px `#2997ff` avec offset 2px. Visible uniquement au clavier (`:focus-visible`).

### Chips (header, filtres, statuts)
- **Style:** Hauteur 26px, padding `0 10px`, radius 8px, fond `rgba(255,255,255,0.04)`, bordure 1px `rgba(255,255,255,0.08)`, backdrop-filter `blur(12px) saturate(160%)`.
- **Label:** Inter 600, 0.66rem, `letter-spacing: 0.08em`, UPPERCASE.
- **Dot:** Petit cercle 5×5 coloré pour signaler un statut (orange en mode paper, vert en live).
- **State:** Hover transform translateY(-1px) + bordure colorée selon la sémantique du chip.

### Cards / Containers
- **Corner Style:** 22px (`--ui-radius`).
- **Background:** `#1d1d1f` au repos, `#2a2a2c` au hover si interactive.
- **Shadow Strategy:** Aucune au repos. Bordure `rgba(255,255,255,0.08)` 1px en lieu et place.
- **Internal Padding:** 22px (default), 14px (compact), 28px (comfortable). Modulable via `body[data-density]`.
- **Nested cards: forbidden.** Une carte ne contient jamais une autre carte.

### Inputs / Fields
- **Style:** Fond `#1d1d1f`, bordure 1px `rgba(255,255,255,0.08)`, radius 8px, padding `10px 12px`, texte `#f5f5f7`.
- **Focus:** Bordure `#2997ff`, halo `0 0 0 3px rgba(41,151,255,0.18)`.
- **Placeholder:** `#6e6e73`.
- **Disabled:** Opacity 0.5, `cursor: not-allowed`.

### Navigation
- **Global header:** Position fixed top, hauteur 68px, fond `rgba(0,0,0,0.82)` + backdrop-filter `blur(22px) saturate(180%)`, bordure basse `rgba(255,255,255,0.05)`.
- **Active link:** Underline 2px `#f5f5f7` en bas du link, animation `navSlide 0.3s ease`.
- **Mobile:** Bottom tab bar dédiée, ne pas réduire le header desktop. Tap-targets ≥44px.

### Signature: Numeric KPI Card
Le pattern le plus important du système : une carte qui affiche un chiffre central, son label en dessous, et une variation à côté.
- Chiffre : Numeric token (Inter 600, 1.5rem, `tnum`).
- Label : Label token (uppercase, 0.66rem, gris secondary).
- Variation : couleur signal (vert/rouge) + signe explicite + icône directionnelle.
- Pas d'icône décorative, pas de mini-sparkline collée. Si on veut un sparkline, il prend toute la carte.

## 6. Do's and Don'ts

### Do:
- **Do** utiliser les chiffres tabulaires (`font-feature-settings: 'tnum'`) sur tout nombre affiché dans une table, une liste ou un KPI.
- **Do** doubler chaque couleur sémantique d'un signe (`+`/`-`) ou d'une icône directionnelle (`▲`/`▼`).
- **Do** garder les bordures à 1px max et toujours en `rgba(255,255,255,0.08)` ou similaire transparent.
- **Do** utiliser le radius 22px (`--ui-radius`) sur les surfaces principales et 8px sur les chips/inputs.
- **Do** respecter `prefers-reduced-motion` et les toggles `.no-aurora`, `.no-grain`, `.no-anim` du body.
- **Do** prendre 22px minimum de padding interne sur les cartes (sauf mode compact à 14px).
- **Do** placer les actions destructives (kill switch, fermeture de position) en pattern neutre au repos, glow rouge au hover seulement.

### Don't:
- **Don't** utiliser `#000` ou `#fff` purs dans le contenu. Toujours tinter (`#f5f5f7` pour le texte, fond reste `#000` par décision technique).
- **Don't** imbriquer une carte dans une autre carte. Si vous avez ce besoin, repensez la hiérarchie.
- **Don't** mettre de bordure colorée plus épaisse qu'1px en accent latéral (`border-left: 4px solid red`). Interdit.
- **Don't** utiliser de gradient sur du texte (`background-clip: text`). Solid color uniquement, contraste par poids.
- **Don't** ajouter de gamification : pas de confettis sur un +5%, pas de sons de succès, pas d'emojis dans les notifications. PortfolioQuant n'est pas Robinhood.
- **Don't** ajouter une 5e teinte de gris à la stack (`#000` / `#101010` / `#1d1d1f` / `#2a2a2c`). Si vous avez besoin de plus, vous découpez mal.
- **Don't** utiliser purple ou teal dans le chrome de l'UI - ils sont réservés aux séries de charts.
- **Don't** afficher un chiffre clé (perf, valeur portfolio) en moins de Numeric token (1.5rem, 600). Sinon ce n'est pas un KPI, c'est un détail.
- **Don't** mettre d'animation au repos sur le contenu critique. Les seules animations tolérées au repos sont l'aurora ambiante et le grain - tous deux désactivables.
- **Don't** densifier au point de violer la min-tap-target de 44px sur tactile.
