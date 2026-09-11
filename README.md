# Strategy Tournament (paper trading)

Système de paper trading uniquement (**aucun ordre réel, aucun argent réel**) qui fait
tourner un tournoi de variantes de stratégies (Casino, Machine Learning, The Economist),
chacune avec son propre portefeuille papier, noté en performance ajustée du risque.

Projet indépendant de l'app existante `PortfolioVirtualTwin` (qui reste intacte, port
3000/8000). Cette app tourne sur le **port 3001** (frontend) / **8001** (backend), pour
pouvoir lancer les deux en parallèle sans conflit de port.

> Simulation — aucun conseil en investissement.

## État actuel : Phase 5

### Phase 1 — fondation (terminée)
- **Données** : cours réels via `yfinance`, avec repli déterministe vers un flux mock
  clairement étiqueté (`source: "mock"`) si yfinance échoue — l'app reste utilisable hors
  ligne, jamais aux dépens de la transparence.
- **Moteur de paper trading** (`app/paper/broker.py`) : simule spread bid-ask, slippage et
  fills partiels au lieu de remplir 100 % au prix coté — sinon les résultats papier ne
  ressemblent pas à du trading réel (point aveugle explicitement signalé dans la spec).
- **Comptabilité** (`app/paper/accounting.py`) : équité, cash, P&L réalisé/latent, drawdown,
  volatilité, Sharpe, Sortino par variante.
- **Garde-fous** : perte max obligatoire avant tout achat, kill-switch global qui bloque
  tous les nouveaux ordres, journal d'exécution append-only.

### Phase 2 — les 3 moteurs de stratégie, version règles simples (terminée)
- **Bibliothèque de signaux** (`app/signals/`) : `market.py` calcule des signaux réels
  dérivés des prix/volumes yfinance (momentum, RSI, volatilité réalisée, volume anormal,
  breakout, momentum de panier thématique). `proxy.py` fournit les familles de signaux
  sans flux réel branché (news, catalyseurs, macro, fondamentaux, microstructure
  options) — **toujours étiquetées `is_proxy: true`**, jamais mélangées silencieusement
  aux signaux réels. Chaque idée affiche le détail facteur par facteur avec ce tag.
- **Casino** (`app/strategies/casino.py`) : catalyseur imminent + volume anormal réel +
  liquidité/spread proxy comme filtres avant score ; structures long-premium uniquement
  (call/put, debit spreads, straddle/strangle, calendar) — jamais de vente d'option nue.
  Ne force aucune idée quand les filtres réels ne sont pas satisfaits (comportement
  observé : univers actuel sans volume ≥2× la moyenne 20j → liste vide, c'est voulu).
- **ML baseline** (`app/strategies/ml_baseline.py`) : composite à poids fixes (prix réel
  35 %, fondamentaux/options/news/macro proxy) en attendant le vrai modèle entraîné de
  la phase 4 — sortie conforme à la spec (action, mouvement prédit, confiance,
  volatilité attendue, structure, raison, risque — jamais un impératif « achète X »).
- **The Economist** (`app/strategies/economist.py`) : 6 thèmes macro curés → paniers
  d'actions, momentum sectoriel réel (moyenne du panier) + alignement/fondamentaux
  proxy ; structures toujours longue échéance (LEAPS, debit spread, collar) — jamais
  de structure courte échéance, pour éviter le piège « souvent tôt » signalé par la spec.
- **Exécution** : `POST /api/strategies/{engine}/ideas/{symbol}/execute` route l'idée
  dans le moteur de paper trading de la phase 1 (perte max = budget de taille × équité)
  et journalise le raisonnement dans `DecisionLog` (`GET /api/variants/{id}/decisions`).
- **Limite connue assumée** : pas de chaîne d'options réelle en V1. La couche 3 choisit
  bien QUEL type de structure utiliser (raisonnement réel et audité), mais l'exécution
  papier se fait sur l'action sous-jacente en proxy de notionnel — les contrats
  d'options réels sont un travail futur, pas simulé pour ne pas fabriquer de fausses
  données de Greeks/IV.
- **UI** : page par moteur (`/strategies/{engine}`) listant les idées classées avec
  raison/invalidation/risque et détail des facteurs réel-vs-proxy, exécution en un clic
  vers la variante choisie ; journal des décisions sur la page de détail variante.

### Phase 3 — 14 variantes en tournoi, cycle propose→exécute→score→tue→scale→génère (terminée)
- **Sous-variantes nommées** (`app/tournament/variants_config.py`) : chaque moteur de la
  phase 2 tourne en plusieurs variantes réellement différentes (même code, poids/filtres/
  structure différents) — Casino A-D (Earnings Straddles, Momentum Call Spreads,
  Short-Squeeze Calls, Post-News Reversal Puts), ML A-E (Momentum Only, Fondamentaux+Prix,
  Sentiment News+Options, Régime Macro+Rotation, Ensemble), Economist A-E (un thème macro
  chacun, dont un nouveau thème « Bénéficiaires de baisse de taux »). 14 variantes au total.
- **Amorçage du roster** : `POST /api/tournament/seed` crée les 14 variantes + leurs
  portefeuilles papier (idempotent — ne recrée pas ce qui existe déjà).
- **Cycle du tournoi** (`app/tournament/cycle.py`, `POST /api/tournament/cycle/run`) :
  propose jusqu'à 2 idées par variante active → exécute en paper → snapshot → classe par
  P&L (Sharpe une fois assez d'historique multi-jours — impossible à obtenir en une seule
  session de test, donc P&L sert de repère provisoire, pas un raccourci permanent) → tue
  (P&L ≤ -8 %) ou scale (+10 % de cash, P&L ≥ +5 %) les variantes ayant ≥3 trades clôturés
  (sous ce seuil, pas assez de données pour juger — pas d'optimisation prématurée).
  « Générer une nouvelle variante depuis une gagnante » est implémenté comme un clone de
  capital (même config, portefeuille neuf) plutôt qu'une mutation de paramètres façon
  algo génétique — muter des poids sans optimisation réelle derrière serait du théâtre ;
  c'est le rôle du modèle ML entraîné de la phase 4.
- **Métriques** (`app/paper/accounting.py`) : hit rate, profit factor, gain/perte moyen
  ajoutés, calculés sur le P&L réalisé réel par ordre de vente (pas de Greeks/vega/theta —
  ça nécessiterait une vraie chaîne d'options qu'on n'a pas, cf. limite phase 2).
- **Dashboard** (`/`) : classement de toutes les variantes (actives et tuées, jamais
  masquées) trié par performance ajustée du risque, code couleur, boutons « Amorcer le
  roster » et « Lancer un cycle maintenant ».
- **Vérifié en direct** : roster amorcé, deux cycles lancés depuis l'UI, positions/journal
  des décisions/journal des ordres cohérents entre eux. 14 tests backend passent.

### Phase 4 — ranking ML entraîné (terminée)
- **Facteurs partagés** (`app/ml/features.py`) : la fonction `compute_factors` qui
  calculait les 5 facteurs de l'ML baseline (`price_trend`, `fundamentals`, `options`,
  `news`, `macro`) directement dans `ml_baseline.py` a été extraite avec un paramètre
  `as_of` — c'est ce qui la rend réutilisable telle quelle pour générer des exemples
  d'entraînement historiques point-in-time (aucune fuite du futur), au lieu de dupliquer
  la logique de scoring entre l'inférence et l'entraînement.
- **Dataset historique** (`app/ml/dataset.py`) : fenêtre glissante sur ~6 ans d'historique
  réel (yfinance, repli mock par symbole) par pas de 5 jours de bourse ; label = rendement
  futur réel à 20 jours. Les familles proxy (fondamentaux/options/news) sont statiques
  par symbole et le macro ne varie que par jour calendaire — le dataset les évalue tels
  quels, sans fabriquer une variation temporelle qu'ils n'ont pas réellement.
- **Entraînement walk-forward** (`app/ml/train.py`, `POST /api/ml/train` ou
  `uv run python -m app.ml.train`) : split chronologique (pas de mélange aléatoire —
  ce serait de la fuite), régression Ridge, coefficients clampés à ≥0 puis renormalisés
  pour devenir les nouveaux poids par défaut des mêmes 5 facteurs — `weighted_score`,
  `apply_weight_overrides` et les 5 variantes ML nommées (Momentum Only, Ensemble, ...)
  restent inchangés, seul le poids par défaut change. Artefact sauvegardé en JSON lisible
  (`app/ml/artifacts/ranking_model.json`, non versionné), pas de pickle.
- **Inférence** (`app/ml/inference.py`) : charge l'artefact JSON (pas de dépendance
  scikit-learn au runtime, juste un produit scalaire + intercept) ; `ml_baseline.py`
  retombe sur les poids fixes d'origine si aucun modèle n'a encore été entraîné, avec
  le même principe de repli explicite que le flux de prix mock. L'artefact garde deux
  jeux de coefficients : `weights` (clampés ≥0 et renormalisés, pour le score composite —
  `weighted_score` suppose des contributions non négatives) et `raw_coefficients` (les
  coefficients réels, signe compris, utilisés pour `predicted_move_pct` — les confondre
  aurait silencieusement faussé la prédiction dès qu'un coefficient réel est négatif,
  ce qui est justement le cas ici).
- **UI** : panneau sur `/strategies/ml` (date d'entraînement, échantillons, précision
  directionnelle, Spearman, poids appris par facteur) avec bouton « Entraîner le
  modèle » ; les idées et leur raisonnement citent le modèle actif.
- **Premier résultat honnête** (univers des 16 tickers d'origine, 4304 exemples) :
  précision directionnelle walk-forward 54% (à peine au-dessus du hasard) et Spearman
  ≈ 0.007 (quasi nul) — `price_trend` clampé à 0% par la régression au profit de
  `macro`/`fundamentals`, qui bien que proxy varient assez pour capter un peu de
  variance. Limite attendue et documentée depuis la phase 2 : sans flux réel
  news/macro/fondamentaux, un modèle entraîné ne peut pas dépasser ce que les données
  réellement variables dans le temps permettent.
- **Amélioration testée en suivi** (même session) : (1) `app/data/macro.py` remplace le
  hash calendaire du facteur `macro` par un régime réel — VIX, taux 10 ans, or, pétrole,
  dollar via yfinance (mêmes familles que PortfolioVirtualTwin), point-in-time correct,
  repli sur le hash proxy si le fetch échoue entièrement (`is_proxy` du facteur reflète
  maintenant la vraie source) ; (2) univers ML élargi de 16 à 101 grandes capitalisations
  (`ml_baseline.UNIVERSE`) pour plus de puissance statistique. Au passage, un vrai bug a
  été trouvé et corrigé : `sample_every=5` avec un horizon de 20 jours faisait se
  chevaucher les fenêtres de rendement futur à 75% — `n_samples` semblait 4× plus grand
  qu'il ne l'était vraiment en information indépendante. `sample_every` vaut maintenant
  `horizon_days` par défaut (fenêtres non chevauchantes).
  **Résultat** : 6868 exemples, précision directionnelle 53.7%, Spearman 0.064 (9× mieux
  qu'avant, mais toujours faible) — les 5 coefficients réels sont légèrement négatifs
  (léger effet de retournement à court terme plutôt que de momentum, un résultat réel
  et documenté dans la littérature à cet horizon, pas un bug : corrélations marginales
  vérifiées une à une, entre -0.05 et -0.004). `weights` retombe donc sur les poids par
  défaut (tous les coefficients sont clampés à 0), mais `raw_coefficients`/
  `predicted_move_pct` reflètent bien le signe réel appris. Prochain levier le plus
  probable : un vrai flux fondamentaux/news temporel, pas seulement plus de tickers.
- **Vérifié en direct** : `uv run pytest` (25 tests, dont `test_macro.py`) verts,
  `uv run python -m app.ml.train` exécuté contre yfinance réel avec le nouvel univers,
  `POST /api/ml/train` et le bouton UI testés de bout en bout — nouveaux poids/métriques
  et badge « réel » sur le facteur macro reflétés dans les idées après entraînement.

### Phase 5 — explications agentiques (terminée)
- **Ce qui change, et ce qui ne change pas** : les moteurs de règles
  (`app/strategies/*`) décident toujours de tout — symbole, structure, score, facteurs.
  `app/agent/explain.py` ne fait que reformuler cette décision déjà figée en prose plus
  riche ; il n'influence jamais ce qui est proposé ou exécuté. Appelé uniquement au
  moment où une idée devient une décision réelle (`DecisionLog`), pas pendant le simple
  parcours des idées (`GET /api/strategies/{engine}/ideas` reste sans appel LLM) — pas
  besoin d'expliquer des candidats que personne n'a exécutés.
- **Modèle : Haiku 4.5**, pas pour le coût (négligeable dans tous les cas à ce volume —
  quelques centimes par cycle de 14 variantes, même avec Opus) mais pour la latence : le
  cycle du tournoi appelle ça de façon synchrone à travers jusqu'à 14 variantes.
- **Appel simple, pas de boucle d'outils** — reformuler des données déjà calculées n'est
  pas une tâche de raisonnement agentique. **Structured output** (`output_format` avec un
  modèle Pydantic à 3 champs — `rationale`/`invalidation`/`main_risk`) calqué exactement
  sur les colonnes `DecisionLog` existantes, remplace directement les strings templatées
  sans parsing fragile.
- **Repli si le LLM échoue** (pas de clé, réseau, rate limit, timeout 15s) : le texte
  templaté d'origine du moteur de règles, inchangé — même principe de repli explicite
  que yfinance→mock et Tiingo/macro. `DecisionLog.explanation_source` (`llm` | `template`)
  trace laquelle des deux a produit le texte, affiché en badge sur la page variante.
- **Mise en cache du prompt — correction d'une estimation faite en conversation** : le
  seuil minimum de préfixe cacheable pour Haiku 4.5 est de 4096 tokens, largement plus
  qu'un system prompt normal pour une tâche de reformulation à 3 champs. Le cache ne
  s'active donc presque certainement pas à cette taille de prompt — `cache_control` est
  quand même posé sur le bloc system (gratuit à inclure, s'activerait automatiquement si
  le prompt dépassait un jour ce seuil), mais aucune conception n'a été faite en pariant
  sur des économies de cache.
- **Migration sans Alembic** : ce projet n'a pas de système de migration, et le fichier
  SQLite de dev existant contenait déjà des données de test des phases précédentes.
  `Base.metadata.create_all()` ne modifie jamais une table existante — `init_db()` fait
  donc un petit ajout de colonne conditionnel en SQL brut (`PRAGMA table_info` puis
  `ALTER TABLE` si absente) pour ajouter `explanation_source` sans supprimer les données
  de test existantes.
- **Vérifié en direct** : `uv run pytest` (28 tests, dont `test_explain.py`, entièrement
  mockés — aucune clé Anthropic nécessaire pour les tests) verts ; sans
  `ANTHROPIC_API_KEY`, exécution d'une idée ML confirmée en `DecisionLog` avec
  `explanation_source: "template"` (comportement inchangé, vérification à vide) ; badge
  « modèle » vérifié dans l'UI sur `/variants/{id}`.

### Backtest historique (`app/backtest/`, terminé)
- **Pourquoi** : le cycle en direct ne propose que 2 idées par variante par lancement
  manuel, avec tue/renforce qui exige au moins 3 trades — à ce rythme, juger si une
  approche a un vrai edge prendrait des mois. Le backtest rejoue les 3 mêmes moteurs +
  la même logique tue/renforce sur des années d'historique réel, en un seul appel
  bloquant (`POST /api/backtest/run`, page `/backtest`).
- **Point-in-time correct** : `casino.py`/`economist.py`/`ml_baseline.py` acceptent
  maintenant `histories`/`as_of` en paramètres optionnels (comportement live inchangé si
  omis) — leurs signaux étaient déjà des fonctions pures de l'historique qu'on leur donne,
  il ne manquait que le point d'entrée pour leur donner un historique tronqué à une date
  passée plutôt que « aujourd'hui ».
- **Achat seul, comme le direct** : rien dans l'appli ne vend automatiquement (seul un
  formulaire manuel le fait) — le backtest reproduit fidèlement ce comportement plutôt que
  d'inventer une règle de sortie qui n'existe pas en direct. Hit rate et profit factor
  restent donc vides, honnêtement, comme en direct.
- **Deux vrais bugs trouvés en construisant le backtest, corrigés des deux côtés
  (direct + backtest)** :
  1. Le seuil tue/renforce (`MIN_TRADES_TO_JUDGE`) comptait les trades *clôturés*
     (vendus) — comme rien ne vend jamais automatiquement, ce compteur restait à 0 pour
     toujours, donc tue/renforce ne se déclenchait quasiment jamais. Corrigé dans le
     backtest en comptant les achats placés (`n_fills`) à la place — une divergence
     assumée et documentée par rapport au direct, qui a le même trou non résolu.
  2. Le boost de cash de « scale » (+10 % du capital initial) n'avait **aucun garde-fou**
     contre un nouveau déclenchement à chaque cycle suivant — une variante modestement
     profitable recevait donc de l'argent gratuit indéfiniment. Un premier run de backtest
     a montré une variante dont ~40 % de l'équité finale venait de ces injections
     répétées, pas de vraies décisions. `Variant.scaled` (nouvelle colonne, migration
     SQLite à la volée comme `explanation_source`) et son équivalent en mémoire
     `BtPortfolio.scaled` limitent maintenant le boost à une seule fois — corrigé dans
     `app/tournament/cycle.py` **et** dans le backtest.
- **Biais in-sample assumé, pas corrigé** : le modèle Ridge du Matheux a été entraîné sur
  à peu près la même fenêtre historique que le backtest rejoue — contrairement au
  Flambeur et au Stratège (formules statiques jamais calées sur aucune période), ses
  résultats ici bénéficient donc d'un avantage que les deux autres n'ont jamais eu.
  Signalé clairement dans la réponse API et l'UI plutôt que corrigé — corriger ça
  demanderait un ré-entraînement point-in-time à chaque date, hors périmètre pour
  l'instant.
- **Marquage quotidien de l'équité, rééquilibrage plus rare** : générer des idées (calcul
  de facteurs sur tout l'univers) est l'étape coûteuse, donc ça ne tourne que tous les
  `rebalance_every_days` jours ; mais marquer l'équité au marché est bon marché, donc ça
  se fait chaque jour de bourse — sinon un vrai creux qui se serait résorbé entre deux
  rééquilibrages passerait inaperçu, et le Sharpe/max drawdown seraient faux. Un premier
  brouillon avec marquage au même rythme que le rééquilibrage donnait un Sharpe de 9.4 et
  un Sortino de 151 sur une stratégie de momentum ordinaire — clairement faux, dû à
  l'annualisation √252 appliquée à des rendements ~10 jours plutôt que journaliers.
  `app/paper/metrics.py` (nouveau module, formules pures partagées avec
  `app/paper/accounting.py` — plus de risque de divergence entre les deux) accepte
  maintenant un vrai `periods_per_year`.
- **Vérifié en direct** : `uv run pytest` (33 tests, dont `test_backtest.py`, entièrement
  mockés) verts ; un run réel contre yfinance (1 an, rééquilibrage tous les 10 jours,
  ~130 titres, 14 variantes) prend environ 3 minutes et donne des résultats plausibles
  après les corrections ci-dessus (avant : +85 % gonflé par le bug de scale ; après :
  +15 % avec un Sharpe ~1.2, cohérent avec une stratégie momentum réelle).

## Lancer en local

### Backend (FastAPI, port 8001)

```bash
cd backend
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

### Frontend (Next.js, port 3001)

```bash
cd frontend
npm install
npm run dev
```

Puis ouvrir http://localhost:3001. Le frontend appelle l'API sur `http://localhost:8001`
(configurable via `NEXT_PUBLIC_API_URL`).

### Tests backend

```bash
cd backend
uv run pytest
```

## Prochaines phases

Les 5 phases de la spec initiale sont terminées. Aucune phase 6 n'est définie pour
l'instant — pistes envisagées en cours de route : flux fondamentaux/news temporel réel
(voir Phase 4), univers élargi pour Casino/Economist.
