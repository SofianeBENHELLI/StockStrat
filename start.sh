#!/usr/bin/env bash
# StockStrat sur macOS / Linux : l'API (port 8001) et l'interface (port 3001).
#
#   ./start.sh            lance les deux, journaux dans ./logs, Ctrl-C pour arrêter
#   KEEP_AWAKE=0 ./start.sh   sans empêcher la mise en veille
#
# Les modèles en paper décident chaque jour peu avant la clôture de Wall Street
# (vers 21h45, heure de Paris). Si le Mac dort à ce moment-là, rien ne se passe
# ce jour-là : par défaut, le script garde le Mac éveillé tant qu'il tourne
# (caffeinate -i). Ce n'est pas un réglage système : ça s'arrête avec le script.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOGS="$ROOT/logs"
mkdir -p "$LOGS"

if [ ! -x "$ROOT/backend/.venv/bin/python" ]; then
  echo "Installation des dépendances Python…"
  (cd "$ROOT/backend" && uv sync)
fi
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "Installation des dépendances de l'interface…"
  (cd "$ROOT/frontend" && npm install)
fi

port_busy() { lsof -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
keep_awake() {
  if [ "${KEEP_AWAKE:-1}" = "1" ] && command -v caffeinate >/dev/null 2>&1; then caffeinate -i "$@"; else "$@"; fi
}

pids=()
cleanup() { [ ${#pids[@]} -gt 0 ] && kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup INT TERM EXIT

if port_busy 8001; then
  echo "API déjà lancée sur le port 8001 — laissée telle quelle."
else
  (cd "$ROOT/backend" && keep_awake .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 \
      >>"$LOGS/api.log" 2>&1) &
  pids+=($!)
fi

if port_busy 3001; then
  echo "Interface déjà lancée sur le port 3001 — laissée telle quelle."
else
  (cd "$ROOT/frontend" && npm run dev >>"$LOGS/web.log" 2>&1) &
  pids+=($!)
fi

echo
echo "  StockStrat   http://localhost:3001"
echo "  API          http://localhost:8001/docs"
echo "  Journaux     $LOGS"
echo
echo "  Ctrl-C pour tout arrêter."
wait
