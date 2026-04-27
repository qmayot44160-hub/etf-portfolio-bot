# Contribuer a PortfolioQuant

Merci de t'interesser au projet ! Voici comment proposer une contribution.

## Avant de commencer

- Verifie qu'une issue n'existe pas deja pour ton sujet
- Pour une grosse feature, ouvre une issue de discussion avant de coder
- Pour un bug, ouvre une issue avec les etapes de reproduction

## Workflow

1. Fork le repo
2. Cree une branche depuis `main` : `git checkout -b feat/ma-feature` ou `fix/mon-bug`
3. Code ta modif
4. Teste localement (`python app.py`)
5. Commit avec un message clair (voir conventions ci-dessous)
6. Push sur ton fork
7. Ouvre une PR vers `main` avec une description du changement

## Conventions de code

### Style d'ecriture

- **Pas de tirets cadratins** (em-dash). Utiliser des tirets simples ou virgules.
- Commentaires en francais pour l'explicatif, anglais pour le technique.
- Pas de tournures emphatiques type "voici", "n'hesitez pas".

### Python

- PEP 8 (tabs ou 4 espaces, pas de mix)
- Docstrings sur les fonctions publiques
- Type hints encourages mais pas obligatoires

### Frontend (`templates/index.html`)

- C'est un fichier unique de ~13k lignes, on garde cette architecture pour l'instant
- Variables CSS : prefixe `--sf-` (ex: `--sf-bg`, `--sf-blue`)
- Mobile breakpoint : `@media (max-width: 720px)`
- Toute feature visible doit etre responsive desktop + mobile
- Preferer SVG natif aux libs externes pour les viz simples
- Les vues sont des `<div id="view-xxx">`, switch via `showView('xxx')`

### Multi-user (en cours)

L'app est mono-utilisateur aujourd'hui. Toute nouvelle feature qui persiste de
la donnee user doit utiliser `data_paths.user_data_path()` plutot que
`data_path()` directement, pour preparer le multi-user.

## Commits

Format simple, description claire en francais :

```
Fix : barre de recherche tronquee sur desktop
Ajout : connecteur Coinbase basique
Refactor : extrait la logique DCA dans un module dedie
Doc : explique les variables d'env requises
```

Pas de Conventional Commits stricts requis. Un commit par changement coherent.

## Tests

Pas de framework de tests automatises pour l'instant (a venir). Pour les PRs,
decris dans la description ce que tu as teste manuellement.

## Securite

Si tu trouves une faille de securite, **n'ouvre pas d'issue publique**.
Contacte le mainteneur en prive (email dans le profil GitHub) pour un disclosure
responsable.

## Questions

Ouvre une issue avec le tag `question`, ou rejoins la discussion sur le repo.
