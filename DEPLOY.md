# Faire tourner StockStrat en continu

Les modèles décident chaque jour vers 21h45 (heure de Paris) et le marché ouvre à 15h30.
Il faut donc une machine allumée à ces heures-là. Deux options.

## Option A — un petit serveur (recommandé)

Un serveur virtuel à ~5 €/mois (Hetzner CX22, Scaleway DEV1-S, OVH VPS…) tourne en
permanence, redémarre les services tout seul, et ne dépend pas de l'état de ton Mac.
Rien n'est exposé sur internet : l'accès passe par **Tailscale**, un réseau privé
entre tes appareils (gratuit pour un usage personnel).

1. **Serveur** : une Debian ou Ubuntu récente, puis installer Docker
   (`curl -fsSL https://get.docker.com | sh`).
2. **Code** : `git clone https://github.com/SofianeBENHELLI/StockStrat && cd StockStrat`
3. **Clé de chiffrement** : `cp .env.server.example .env`, puis y mettre
   `STOCKSTRAT_SECRET_KEY`. Si tu migres depuis le Mac, reprends **le contenu de
   `backend/.secret_key`** : c'est lui qui déchiffre les clés Alpaca déjà enregistrées.
4. **Tailscale** : crée une clé sur <https://login.tailscale.com/admin/settings/keys>, mets-la
   dans `TS_AUTHKEY` du fichier `.env`, installe l'appli Tailscale sur ton téléphone.
5. **Démarrer** : `docker compose --profile tailscale up -d --build`
6. **Ouvrir** : `https://stockstrat.<ton-réseau>.ts.net` depuis n'importe quel appareil de
   ton réseau Tailscale — y compris le téléphone.

### Reprendre l'historique du Mac

Arrêter le Mac d'abord (sinon deux instances trading en même temps), puis :

```bash
# sur le Mac
cd StockStrat/backend && sqlite3 strategy_tournament.db ".backup /tmp/stockstrat.db"
scp /tmp/stockstrat.db serveur:/tmp/
# sur le serveur
docker compose cp /tmp/stockstrat.db api:/data/stockstrat.db
docker compose restart api worker
```

### Au quotidien

- **Mise à jour** : `git pull && docker compose --profile tailscale up -d --build`
- **Journaux** : `docker compose logs -f worker`
- **Sauvegardes** : quotidiennes, 14 jours, dans le volume (`/data/backups`). Pour en
  récupérer une : `docker compose cp api:/data/backups/<fichier> .`
- **Surveillance** : renseigne une URL de ping (healthchecks.io, gratuit) dans
  Administration → Monitor. Si le serveur tombe, c'est ce service qui te prévient.

## Option B — rester sur le Mac

```bash
ops/macos/install.sh
```

Installe trois services macOS (worker, API, interface) lancés à l'ouverture de session et
relancés s'ils plantent. Le worker empêche la mise en veille tant qu'il tourne, mais un
capot fermé endort quand même le Mac : à 21h45, il doit être ouvert et branché.
`ops/macos/uninstall.sh` pour revenir en arrière.

Sans installation : `./start.sh` (production) ou `DEV=1 ./start.sh` (développement).

## Pourquoi l'option A

| | Serveur | Mac |
|---|---|---|
| Décisions de 21h45 garanties | oui | seulement si le Mac est allumé et ouvert |
| Stops de clôture | tous les jours | idem |
| Stops de secours chez Alpaca | oui | oui (ils tiennent même Mac éteint) |
| Accès depuis le téléphone | oui (Tailscale) | non, sauf à installer Tailscale sur le Mac |
| Coût | ~5 €/mois | 0 |
