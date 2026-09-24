# StockStrat — labo de stratégies et paper trading

Trois profils d'investissement, testés sur dix ans de données réelles, puis lâchés
sur un vrai compte de **paper trading Alpaca**, chacun avec son propre budget, pour
voir si ce qu'ils promettaient en backtest tient face au marché.

> Paper trading uniquement : aucun argent réel. Rien ici n'est un conseil en investissement.

## Démarrer

```bash
./start.sh
```

Puis ouvrir <http://localhost:3001>. Au premier lancement : **Administration → Connexions**,
coller l'identifiant et la clé secrète **paper** d'Alpaca, puis « Tester la connexion ».
Le script garde le Mac éveillé tant qu'il tourne : les modèles décident chaque jour
vers 21h45 (heure de Paris), et un Mac en veille ne décide rien.

## Le cycle

1. **Laboratoire** — un *modèle* = un profil + des paramètres + un budget. Chaque
   paramètre est exposé, borné et expliqué ; l'écran est généré depuis le schéma servi par l'API.
2. **Backtest** — sur 10 ans de barres journalières Alpaca (flux consolidé SIP, ajustées
   des splits et dividendes). Résultat en dollars : gain, pire creux.
3. **Classement** — tous les modèles sur la même période et le même budget, avec une
   colonne **Déployer**.
4. **Paper** — le modèle ouvre une sous-comptabilité sur le compte Alpaca paper et trade
   seul. Sa page compare en permanence le réel au **backtest rejoué sur les mêmes jours**.
5. **Optimiseur** — teste toutes les combinaisons de paramètres d'un profil et dit si le
   classement du passé prédit celui du futur.

## Les trois profils

| Profil | Décide | Tient | Ce qu'il fait | Univers |
|---|---|---|---|---|
| **Le Flambeur** | chaque jour | quelques jours | *Rebond* : achète une action en forte survente (RSI 2 jours très bas) dans une tendance haussière, revend au rebond. *Cassure* : achète le franchissement d'un plus haut sur volume anormal, avec stop suiveur. | 100 grandes capitalisations US |
| **Le Matheux** | chaque mois | ~1 mois | Un modèle de machine learning (Ridge ou arbres boostés), réentraîné chaque mois, classe les titres sur leur performance probable du mois suivant ; il achète les mieux classés. | 100 grandes capitalisations US |
| **Le Stratège** | chaque mois | 3 à 6 mois | Classe des **thèmes** (ETF : pétrole, défense, semi-conducteurs, uranium, or…) sur leur momentum, garde les plus porteurs au moins N mois tant que leur tendance tient, et sort immédiatement si un thème décroche (stop suiveur). | 19 ETF thématiques |

Chaque profil a trois variantes de départ. Les stops sont vérifiés **chaque jour** pour
tous les profils, même ceux qui ne rééquilibrent qu'une fois par mois.

## Pourquoi on peut croire les chiffres (et quand il ne faut pas)

- **Un seul moteur.** `engine.plan()` calcule les ordres ; le backtest les exécute à la
  clôture, le runner paper les envoie à Alpaca. Il n'y a pas de seconde implémentation
  de la logique : un écart entre paper et backtest ne peut venir que du marché et de
  l'exécution.
- **Deux étalons**, affichés à côté de chaque résultat : le S&P 500, et un **placebo** —
  l'univers du profil acheté à parts égales et conservé, sans aucune décision.
- **Le biais du survivant est affiché, pas caché.** L'univers actions, ce sont les géants
  d'aujourd'hui : sur 2019-2026, le placebo bat le S&P 500 de ~13 000 $ sur 10 000 $
  investis sans prendre une seule décision. Un modèle qui ne bat pas le placebo n'a pas de talent.
- **Le Matheux ne voit pas le futur.** Il ne s'entraîne à une date donnée que sur des
  exemples dont le résultat était connu ce jour-là. Un test le prouve : remplacer par du
  bruit toutes les données inconnues à la date de décision laisse la prédiction identique
  au bit près. Le rapport affiche aussi sa corrélation de rang hors échantillon : si elle
  n'est pas significative, l'interface le dit (« non établi »).
- **L'optimiseur mesure son propre surapprentissage** : choix sur 2016-2021, vérification
  sur 2022-aujourd'hui, et corrélation de rang entre les deux.

## Premiers résultats (backtest du 2 janvier 2019 au 24 septembre 2026, 10 000 $ chacun)

| Modèle | Gain | Pire creux | vs S&P 500 | vs placebo |
|---|---:|---:|---:|---:|
| Matheux — Arbres boostés | +52 401 $ | −8 958 $ | +28 041 $ | +15 176 $ |
| Matheux — Linéaire | +43 968 $ | −15 011 $ | +19 609 $ | +6 744 $ |
| Matheux — Concentré et prudent | +33 445 $ | −13 835 $ | +9 086 $ | −3 780 $ |
| Stratège — Top 3 thèmes, 6 mois | +25 743 $ | −7 108 $ | +1 383 $ | −881 $ |
| Stratège — Diversifié avec refuges | +17 621 $ | −6 896 $ | −6 739 $ | −7 276 $ |
| Flambeur — Cassure sur volume | +15 502 $ | −2 982 $ | −8 857 $ | −21 723 $ |
| Stratège — Convictions longues | +13 774 $ | −7 331 $ | −10 585 $ | −12 850 $ |
| Flambeur — Rebond classique | +5 638 $ | −3 099 $ | −18 721 $ | −31 587 $ |
| Flambeur — Rebond concentré | −401 $ | −3 604 $ | −24 761 $ | −37 626 $ |
| *S&P 500 (SPY)* | *+24 360 $* | | | |
| *Placebo actions / ETF* | *+37 225 $ / +26 624 $* | | | |

**Lecture honnête.** Le Matheux bat tout le monde… mais sa corrélation de rang hors
échantillon est ≈ 0 (t ≈ −0,3) : le modèle ne prédit rien de mesurable, sa performance
vient de l'univers et du hasard. Le Stratège fait jeu égal avec le S&P 500 avec un creux
plus faible. Le Flambeur perd face aux deux étalons une fois les coûts payés.

**Optimiseur (choix 2016-2021, vérification 2022 → aujourd'hui).** Stratège : corrélation
passé → futur 0,29 ; la meilleure combinaison du passé finit 4ᵉ sur 108 ensuite. Flambeur :
0,47 ; la meilleure finit 8ᵉ sur 54. Dans les deux cas elle bat le S&P 500 ensuite, et
quasiment aucune combinaison ne bat le placebo.

## Paper trading

- **Un compte Alpaca, une sous-comptabilité par modèle.** Chaque ordre appartient à un
  modèle et porte son étiquette (`ss-m<modèle>-o<ordre>`, visible dans le tableau de bord
  Alpaca). Alpaca ne voit qu'une position par titre ; la **réconciliation** vérifie toutes
  les 5 minutes que la somme des sous-comptabilités égale ce qu'Alpaca détient.
- **Jamais de marge.** La somme des budgets est plafonnée (95 000 $ par défaut) sous le cash
  du compte ; `paper=True` est codé en dur, il n'existe aucun chemin vers le trading réel.
- **Décision une fois par séance**, quelques minutes avant la clôture, sur le dernier prix —
  l'équivalent réel de la clôture du backtest. Aucune décision tant qu'un ordre est en attente
  (il serait acheté deux fois).
- **Des prix Alpaca ou rien.** Sans prix, un ordre est refusé — jamais passé sur un prix inventé.
- **Journal de bord** : chaque décision, ordre, exécution, sortie et erreur, en clair.

## Architecture

```
backend/app/
  lab/            profils, moteur, backtest, métriques en $, runner paper, optimiseur, données Alpaca
  paper/          port broker (simulateur, Alpaca paper), comptabilité, garde-fous pré-trade, réconciliation
  monitor/        la boucle : sondage des ordres, décisions, relevés d'équité, réconciliation
  routers/        API (/api/lab, /api/settings)
frontend/src/app/ salle des marchés, laboratoire, page modèle, optimiseur, classement, page paper, administration
```

Tests : `cd backend && .venv/bin/python -m pytest` (57 tests, dont le test d'empoisonnement
du Matheux et le cycle de vie complet d'un modèle).

## Limites connues

- Biais du survivant sur l'univers actions (affiché, mesuré par le placebo, pas corrigé).
- Pas d'options : les profils tradent des actions et des ETF, en achat seul.
- Pas de flux d'actualités ni de calendrier de résultats : les signaux sont prix et volume uniquement.
- L'application tourne sur le Mac : s'il est éteint à l'heure de décision, la décision du jour est sautée.
- Pas de trading réel : c'est l'étape suivante, et elle demandera sa propre classe de broker,
  ses propres clés et un armement explicite.
