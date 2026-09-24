#!/usr/bin/env bash
# Keep StockStrat running on this Mac without a terminal open.
#
# Installs three per-user launchd agents — worker, API, interface — started at
# login and restarted if they crash. Nothing system-wide, no admin rights.
# The worker is wrapped in `caffeinate -i`, so the Mac does not idle-sleep
# while it runs (closing the lid still sleeps it).
#
#   ops/macos/install.sh      install and start
#   ops/macos/uninstall.sh    stop and remove
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$ROOT/logs"
mkdir -p "$AGENTS" "$LOGS"
NPX="$(command -v npx)"
[ -x "$ROOT/backend/.venv/bin/python" ] || { echo "Lancez d'abord ./start.sh une fois (installation des dépendances)."; exit 1; }
[ -f "$ROOT/frontend/.next/BUILD_ID" ] || (cd "$ROOT/frontend" && npm run build)

agent() {  # label, workdir, env (KEY=VALUE;...), program args...
  local label="$1" dir="$2" envs="$3"; shift 3
  local plist="$AGENTS/$label.plist"
  {
    echo '<?xml version="1.0" encoding="UTF-8"?>'
    echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
    echo '<plist version="1.0"><dict>'
    echo "  <key>Label</key><string>$label</string>"
    echo "  <key>WorkingDirectory</key><string>$dir</string>"
    echo '  <key>ProgramArguments</key><array>'
    for a in "$@"; do echo "    <string>$a</string>"; done
    echo '  </array>'
    echo '  <key>EnvironmentVariables</key><dict>'
    echo "    <key>PATH</key><string>$(dirname "$NPX"):/usr/bin:/bin:/usr/sbin:/sbin</string>"
    IFS=';' read -ra pairs <<< "$envs"
    for kv in "${pairs[@]}"; do [ -n "$kv" ] && echo "    <key>${kv%%=*}</key><string>${kv#*=}</string>"; done
    echo '  </dict>'
    echo '  <key>RunAtLoad</key><true/>'
    echo '  <key>KeepAlive</key><true/>'
    echo '  <key>ThrottleInterval</key><integer>15</integer>'
    echo "  <key>StandardOutPath</key><string>$LOGS/${label##*.}.log</string>"
    echo "  <key>StandardErrorPath</key><string>$LOGS/${label##*.}.log</string>"
    echo '</dict></plist>'
  } > "$plist"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist"
  echo "  installé : $label"
}

agent com.stockstrat.worker "$ROOT/backend" "" /usr/bin/caffeinate -i "$ROOT/backend/.venv/bin/python" -m app.worker
agent com.stockstrat.api "$ROOT/backend" "RUN_SCHEDULER=0" "$ROOT/backend/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8001
agent com.stockstrat.web "$ROOT/frontend" "" "$NPX" next start -p 3001

echo
echo "StockStrat tourne en arrière-plan : http://localhost:3001 (journaux dans $LOGS)."
echo "Après une mise à jour du code : ops/macos/install.sh à nouveau."
