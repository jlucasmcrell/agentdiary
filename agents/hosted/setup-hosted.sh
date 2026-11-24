#!/bin/sh
# Set up (or update) one hosted agent on the server. Run from this folder after copying it to the server:
#   sh setup-hosted.sh <name>          e.g. connector
# The API key is read from stdin if given (printf '%s' "$KEY" | sh setup-hosted.sh connector) and stored in state/api.key, mode 600.
# Idempotent: an existing mind is never re-initialised, and the cron line is added only once.
set -eu
name="$1"
here=$(cd "$(dirname "$0")" && pwd)
agent="$HOME/agentdiary-thinker/agent"        # the shared runner (thinker.py, mind.py, wake-prompt.md)
base="$HOME/agentdiary-agents/$name"
mkdir -p "$base/home" "$base/state"
cp "$here/$name/thinker.json" "$base/thinker.json"
cp "$here/$name/seed.md" "$base/seed.md"
if [ ! -t 0 ]; then
  umask 077; key=$(cat); [ -n "$key" ] && printf '%s' "$key" > "$base/state/api.key"; umask 022
fi
[ -s "$base/state/api.key" ] || echo "warning: no key in $base/state/api.key"
if [ ! -f "$base/state/mind.db" ]; then
  MIND_DB="$base/state/mind.db" python3 "$agent/mind.py" init --seed "$base/seed.md"
fi
touch "$base/state/enabled"
line="*/10 * * * * cd $agent && THINKER_CONFIG=$base/thinker.json /usr/bin/python3 thinker.py maybe-wake >> $base/cron.log 2>&1 # agentdiary-hosted-$name"
( crontab -l 2>/dev/null | grep -v "# agentdiary-hosted-$name\$" ; echo "$line" ) | crontab -
echo "$name ready: $(THINKER_CONFIG=$base/thinker.json python3 $agent/thinker.py status | tr -d '\n ' | cut -c1-120)"
