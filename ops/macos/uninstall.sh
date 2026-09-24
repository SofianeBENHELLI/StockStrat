#!/usr/bin/env bash
# Stop StockStrat's launchd agents and remove them.
set -euo pipefail
for label in com.stockstrat.worker com.stockstrat.api com.stockstrat.web; do
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$label.plist"
  echo "  retiré : $label"
done
