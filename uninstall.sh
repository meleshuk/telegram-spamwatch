#!/bin/bash
# Removes the Telegram spam watcher schedule (keeps login session + config).
set -euo pipefail
PLIST="$HOME/Library/LaunchAgents/com.muse.tgspamwatch.plist"
UID_NUM="$(id -u)"
launchctl bootout "gui/$UID_NUM" "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "Watcher unscheduled. ~/.tgspamwatch/ left intact (session, config, logs)."
echo "Delete that folder too if you want everything gone."
