#!/bin/sh
# Started as root so a freshly created bind mount (owned by root) can be handed to the app user,
# then drops privileges. Set PUID/PGID to match your host user if you want to edit ./data directly.
set -e
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
DATA="${PAINTLOCKER_DATA_DIR:-/data}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA"
    if [ "$(stat -c %u:%g "$DATA")" != "$PUID:$PGID" ]; then
        chown -R "$PUID:$PGID" "$DATA"
    fi
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi
exec "$@"
