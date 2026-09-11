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

### Boucle d'exécution : sorties, port broker, monitor (terminé)

- **Le trou de départ** : rien dans `app/` ne vendait jamais. Un `grep '"sell"'` hors tests ne
  renvoyait rien, et la seule voie de vente était le formulaire manuel de la page variante.
  Conséquences, toutes vérifiées en base : `PaperOrder.realized_pnl` jamais renseigné, donc
  `hit_rate_pct` et `profit_factor` définitivement `null` sur toutes les variantes ; le seuil
  tue/renforce de `cycle.py` conditionné au nombre de trades *clôturés*, donc bloqué à 0 pour
  toujours (le bug déjà corrigé côté backtest, laissé ouvert en direct) ; et du cash qui ne
  faisait que décroître jusqu'à ce qu'une variante ne puisse plus trader. Le tournoi en direct
  était structurellement incapable de boucler son propre cycle.
- **Règles de sortie** (`app/paper/exits.py`) : stop, objectif, horloge — volontairement bêtes.
  Ce n'est pas une stratégie et ça n'essaie pas de l'être : les trois moteurs décident quoi
  acheter, ceci décide seulement quand arrêter de le détenir. Les seuils viennent du panneau
  d'administration, et la raison de chaque sortie est écrite sur l'ordre (`exit_reason`) pour
  que le journal reste auditable. Un symbole sans prix frais est laissé tranquille — sortir sur
  un prix absent serait la pire raison de vendre. Le seuil tue/renforce compte désormais les
  *fills* et non les trades clôturés, alignant le direct sur le backtest.
- **Port broker** (`app/paper/brokers.py`) : `submit` / `poll` / `cancel` renvoyant un objet
  valeur. Trois choix qui ne coûtent rien maintenant et évitent une réécriture plus tard : le
  `market_price` est injecté et jamais récupéré par le broker (c'est déjà ce qui permet au
  backtest de rejouer des fills à une date passée) ; un rejet est une valeur de retour, pas une
  exception, seule une *configuration* invalide lève ; et le broker ne détient aucun état —
  `SimBroker` est sans mémoire, l'état vit en base, donc un redémarrage ne perd rien.
  `simulate_fill` est conservé tel quel : son spread/slippage/fills partiels sont plus riches
  que ce qu'un simulateur écrit de zéro aurait donné.
- **Cycle de vie réel des ordres** : le statut `open` existe enfin. Une limite non franchie
  reste posée au lieu d'être rejetée, et le poller la réévalue contre un prix frais. `cancel`
  existe. Les ordres se règlent tous par la même fonction `app/paper/settlement.apply_fill`,
  idempotente — deux écrivains (une requête HTTP et la boucle de fond) peuvent atteindre la
  même ligne, et un double règlement compterait silencieusement une position deux fois.
- **Bug corrigé au passage** : l'affordabilité était vérifiée *pendant* l'application du fill,
  c'est-à-dire après exécution. Sans conséquence face à un simulateur qu'on peut faire oublier,
  mais face à un vrai venue cela signifie que le trade a eu lieu et que le livre a refusé de
  l'enregistrer. Tous les garde-fous (kill-switch, cash, plafond de notionnel, quantité détenue)
  passent maintenant avant l'appel au broker. Second bug : la ligne `Position` étant réutilisée,
  rouvrir un symbole déjà soldé conservait l'`opened_at` d'origine — la sortie sur durée aurait
  clôturé une position neuve dès le premier jour.
- **Monitor** (`app/monitor/scheduler.py`) : une boucle asyncio, un passage par intervalle, sur
  chaque portefeuille de variante active — sonde les ordres posés, applique les sorties,
  enregistre l'équité. Ce troisième point n'est pas de la comptabilité : Sharpe, Sortino et
  drawdown se calculent sur l'espacement des snapshots, et n'enregistrer l'équité que quand
  quelqu'un clique donne une série ni quotidienne ni régulière — la même classe d'erreur que le
  Sharpe de 9.4 d'un premier brouillon de backtest. Isolation par portefeuille : un symbole
  impossible à valoriser ne doit pas empêcher les treize autres d'être marqués.
- **Panneau d'administration** (`/settings`, `app/routers/settings.py`) : le schéma des réglages
  est *servi*, pas codé en dur dans le frontend — ajouter un réglage est une entrée dans
  `app/core/settings_store.FIELDS` et rien d'autre. Les valeurs de l'environnement restent les
  défauts et une ligne en base les surcharge, donc une installation neuve démarre sans aucune
  ligne. Les secrets sont en écriture seule à travers l'API : on peut poser une clé, jamais la
  relire (`public_view()` ne renvoie que « configuré » et les 4 derniers caractères). Le
  `trading_mode` reste une constante `paper`, qu'aucune route ne peut modifier.
- **Limite assumée** : le simulateur ne vérifie toujours pas les heures de marché, et les
  structures d'options restent exécutées en proxy sur le sous-jacent (cf. limite phase 2) —
  deux points qui devront être tranchés avant de router quoi que ce soit vers un vrai venue.
- **Vérifié en direct** : `uv run pytest` (67 tests, contre 33 avant) verts ; vente manuelle
  exécutée contre yfinance réel, faisant passer `n_closed_trades` de 0 à 1 et `hit_rate_pct` /
  `profit_factor` de `null` à une valeur ; limite non franchie observée au statut `open` puis
  poller la laissant en place, limite franchissable remplie immédiatement ; plafond de notionnel
  et vente à découvert refusés avant tout appel broker ; monitor tournant sur les 14
  portefeuilles en ~1.5 s par passage.

### Connexion Alpaca paper (terminé, non testé contre un vrai compte)

- **`paper=True` est codé en dur** dans `AlpacaPaperBroker` et n'est exposé par aucun réglage,
  aucune variable d'environnement, aucune route. Atteindre le trading réel depuis ce code n'est
  pas une question de basculer un drapeau : il faudrait une autre classe et une autre paire
  d'identifiants que ce code n'a aucun moyen de lire. Il n'y a volontairement pas de champs
  `ALPACA_LIVE_*` — ajouter les réglages avant les garde-fous serait le plus court chemin vers
  un vrai ordre à une faute de frappe près.
- **Deux traductions qui portent tout le risque** : (1) un statut Alpaca inconnu redevient
  `open`. Alpaca a des états que ce code n'a jamais vus, et en traiter un comme terminal
  abandonnerait silencieusement un ordre vivant chez le venue pendant que le livre le croit
  réglé ; le traiter comme ouvert coûte seulement un sondage de plus. (2) `partially_filled`
  n'est **pas** réglé : notre règlement est one-shot par ordre alors qu'Alpaca remplit par
  incréments, donc régler un partiel immédiatement enregistrerait la tranche et ignorerait le
  reste. Un partiel reste `open` et n'est réglé qu'à l'état terminal réel — y compris le cas
  d'une annulation après remplissage partiel.
- **Un refus du venue est une valeur de retour**, pas une exception : pas de pouvoir d'achat,
  symbole non fractionnable, marché fermé — ce sont des issues normales du trading, et lever
  ferait tomber tout le cycle du tournoi pour un seul symbole. Un sondage en échec ne renvoie
  jamais d'état terminal non plus : une coupure réseau ne doit pas s'enregistrer comme une
  annulation.
- **Réconciliation** (`app/paper/venue.py`) : absente de la spec d'origine, indispensable ici.
  Dès que les ordres s'exécutent ailleurs, la base cesse d'être la source de vérité et devient
  une *affirmation* à son sujet. Un livre non réconcilié est un livre qui a l'air juste sur tous
  les tableaux de bord tout en étant faux.
- **Une seule variante à la fois sur le venue.** Un compte Alpaca nette toutes les positions :
  deux variantes longues sur le même symbole n'y font qu'une ligne, et l'attribution par
  variante — le principe même du tournoi — cesse silencieusement d'être réelle. La promotion
  refuse donc une deuxième variante sauf `force`, et la réconciliation signale le cas.
- **Promotion explicite** : router une variante vers le venue exige la chaîne littérale
  `CONFIRM` (HTTP 428 sinon). Tout le reste de l'application est de la comptabilité réversible ;
  ceci est la seule action qui fait sortir des ordres de la machine.
- **Identifiants** : posés dans Administration → Connexions (stockés en base, en écriture seule
  à travers l'API) ou dans `backend/.env`. Un bouton « Tester la connexion » interroge le compte
  et l'horloge de marché sans jamais renvoyer les identifiants.
- **Vérifié** : 84 tests verts, dont 16 dédiés à la traduction des statuts et au garde-fou de
  promotion, tous hors ligne. Chemins d'échec vérifiés en direct (absence d'identifiants, 428
  sans `CONFIRM`, 409 sans identifiants, réconciliation à vide). **Non vérifié : aucun appel
  n'a encore été fait contre un vrai compte Alpaca** — il faut une paire de clés valide pour
  cela.

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

Les 5 phases de la spec initiale sont terminées, ainsi que la boucle d'exécution
(sorties, port broker, monitor, panneau d'administration).

La connexion Alpaca paper est en place mais n'a encore jamais tourné contre un vrai
compte. Ce qui reste ouvert :

- **Heures de marché.** Le simulateur ne les vérifie toujours pas. L'horloge Alpaca est
  lue et affichée par le test de connexion, mais rien ne bloque encore un ordre hors
  séance — côté venue, Alpaca s'en charge ; côté `sim`, l'écart reste.
- **Structures d'options.** Toujours exécutées en proxy sur le sous-jacent (cf. limite
  phase 2). Router cela vers un vrai venue produit un ordre actions étiqueté comme une
  structure d'options dans le journal — à trancher avant de promouvoir quoi que ce soit.
- **Quantités fractionnaires.** Les fills partiels du simulateur produisent des positions
  fractionnaires ; Alpaca ne les accepte que sur les symboles éligibles, en ordre marché
  DAY. Un refus est bien remonté comme rejet, mais la situation n'a pas été observée en réel.
- **Réconciliation automatique.** Elle existe et s'appelle à la demande ; elle n'est pas
  encore branchée dans la boucle du monitor.

Autres pistes envisagées en cours de route : flux fondamentaux/news temporel réel
(voir Phase 4), univers élargi pour Casino/Economist.
