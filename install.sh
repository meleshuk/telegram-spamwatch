#!/bin/bash
# Installs the Telegram spam watcher on your Mac.
# Creates an isolated venv (no system Python touched), installs telethon,
# and schedules the watcher every 5 minutes via launchd.
# Run from the unzipped folder, then run tg_login.py (see README).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
BASE="$HOME/.tgspamwatch"
AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST="$AGENTS_DIR/com.muse.tgspamwatch.plist"

mkdir -p "$BASE" "$AGENTS_DIR"
chmod 700 "$BASE"
cp "$SRC/tg_configure.py" "$SRC/tg_login.py" "$SRC/tg_spamwatch.py" "$BASE/"
if [ ! -f "$BASE/config.json" ]; then
    echo "Telegram API credentials are required for first-time setup."
    echo "Create them at https://my.telegram.org/apps if needed."
    python3 "$BASE/tg_configure.py" --output "$BASE/config.json"
fi
chmod 600 "$BASE/config.json"
[ -f "$BASE/state.json" ] || printf '{}\n' > "$BASE/state.json"
chmod 600 "$BASE/state.json"
[ ! -f "$BASE/session.session" ] || chmod 600 "$BASE/session.session"
touch "$BASE/watcher.log"
chmod 600 "$BASE/watcher.log"

python3 -m venv "$BASE/venv"
"$BASE/venv/bin/pip" install --quiet -r "$SRC/requirements.txt"

PLIST_CANDIDATE="$BASE/com.muse.tgspamwatch.plist.new"
sed "s|__HOME__|$HOME|g" "$SRC/com.muse.tgspamwatch.plist" > "$PLIST_CANDIDATE"
PLIST_CHANGED=0
if [ ! -f "$PLIST" ] || ! cmp -s "$PLIST_CANDIDATE" "$PLIST"; then
    cp "$PLIST_CANDIDATE" "$PLIST"
    PLIST_CHANGED=1
fi
rm -f "$PLIST_CANDIDATE"

UID_NUM="$(id -u)"
if launchctl print "gui/$UID_NUM/com.muse.tgspamwatch" >/dev/null 2>&1; then
    if [ "$PLIST_CHANGED" -eq 1 ]; then
        launchctl bootout "gui/$UID_NUM" "$PLIST"
        launchctl bootstrap "gui/$UID_NUM" "$PLIST"
    fi
else
    launchctl bootstrap "gui/$UID_NUM" "$PLIST"
fi

echo "Installed. The watcher runs every 5 minutes."
echo "Next: log in with:  $BASE/venv/bin/python $BASE/tg_login.py"
echo "Flags go to your Telegram Saved Messages. Logs: $BASE/watcher.log"
