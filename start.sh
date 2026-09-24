#!/usr/bin/env bash
# StockStrat sur macOS / Linux.
#
#   ./start.sh              production : worker de décision + API + interface compilée
#   DEV=1 ./start.sh        développement : boucle dans l'API, rechargement à chaud
#   KEEP_AWAKE=0 ./start.sh sans empêcher la mise en veille
#
# Trois processus en production :
#   - le worker (python -m app.worker) : la boucle qui sonde les ordres et fait
#     décider les modèles vers 21h45 (heure de Paris). C'est lui qui compte :
#     il garde le Mac éveillé tant qu'il tourne (caffeinate -i), car un Mac en
#     veille à l'heure de décision ne décide rien ce jour-là ;
#   - l'API (port 8001, seulement en local) ;
#   - l'interface (port 3001), qui relaie /api vers l'API.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOGS="$ROOT/logs"
mkdir -p "$LOGS"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

if [ ! -x "$BACKEND/.venv/bin/python" ]; then
  echo "Installation des dépendances Python…"
  (cd "$BACKEND" && uv sync)
fi
if [ ! -d "$FRONTEND/node_modules" ]; then
  echo "Installation des dépendances de l'interface…"
  (cd "$FRONTEND" && npm install)
fi

port_busy() { lsof -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
keep_awake() {
  if [ "${KEEP_AWAKE:-1}" = "1" ] && command -v caffeinate >/dev/null 2>&1; then caffeinate -i "$@"; else "$@"; fi
}
worker_running() { pgrep -f "python -m app.worker" >/dev/null 2>&1; }

pids=()
cleanup() { [ ${#pids[@]} -gt 0 ] && kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup INT TERM EXIT

if [ "${DEV:-0}" = "1" ]; then
  port_busy 8001 || { (cd "$BACKEND" && keep_awake .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 \
      --port 8001 --reload --reload-dir app >>"$LOGS/api.log" 2>&1) & pids+=($!); }
  port_busy 3001 || { (cd "$FRONTEND" && npm run dev >>"$LOGS/web.log" 2>&1) & pids+=($!); }
else
  # Recompile the interface only if its sources changed since the last build.
  if [ ! -f "$FRONTEND/.next/BUILD_ID" ] || \
     [ -n "$(find "$FRONTEND/src" "$FRONTEND/next.config.ts" "$FRONTEND/package.json" -newer "$FRONTEND/.next/BUILD_ID" -print -quit)" ]; then
    echo "Compilation de l'interface…"
    (cd "$FRONTEND" && npm run build >>"$LOGS/build.log" 2>&1) || { echo "Échec de compilation, voir $LOGS/build.log"; exit 1; }
  fi
  if worker_running; then
    echo "Worker déjà lancé — laissé tel quel."
  else
    (cd "$BACKEND" && keep_awake .venv/bin/python -m app.worker >>"$LOGS/worker.log" 2>&1) & pids+=($!)
  fi
  if port_busy 8001; then echo "API déjà lancée sur le port 8001 — laissée telle quelle."; else
    (cd "$BACKEND" && RUN_SCHEDULER=0 .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 \
        >>"$LOGS/api.log" 2>&1) & pids+=($!)
  fi
  if port_busy 3001; then echo "Interface déjà lancée sur le port 3001 — laissée telle quelle."; else
    (cd "$FRONTEND" && npm run start >>"$LOGS/web.log" 2>&1) & pids+=($!)
  fi
fi

echo
echo "  StockStrat   http://localhost:3001"
echo "  Journaux     $LOGS"
echo "  Ctrl-C pour tout arrêter."
wait
