#!/bin/sh
# Starts as root so a freshly created bind mount (owned by root) can be handed to the app user,
# then drops privileges. Never fails just because ownership can't be changed (NAS shares, rootless
# Docker, read-only parents): it logs what it found and carries on.
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
DATA="${PAINTLOCKER_DATA_DIR:-/data}"

say() { echo "$(date '+%Y-%m-%d %H:%M:%S') INFO    entrypoint: $*"; }
warn() { echo "$(date '+%Y-%m-%d %H:%M:%S') WARNING entrypoint: $*"; }

say "Paint Locker ${PAINTLOCKER_VERSION:-dev}; data folder $DATA; started as uid $(id -u)"

if [ "$(id -u)" != "0" ]; then
    say "not root (the container's 'user:' is set), running as uid $(id -u) without changing ownership"
    exec "$@"
fi

mkdir -p "$DATA" || warn "couldn't create $DATA"
owner="$(stat -c %u:%g "$DATA" 2>/dev/null)"
if [ "$owner" != "$PUID:$PGID" ]; then
    say "data folder is owned by $owner, handing it to $PUID:$PGID"
    chown -R "$PUID:$PGID" "$DATA" 2>/dev/null || warn "couldn't change ownership of $DATA (network share or restricted mount?)"
fi

if setpriv --reuid="$PUID" --regid="$PGID" --clear-groups sh -c "touch '$DATA/.write-test' && rm -f '$DATA/.write-test'" 2>/dev/null; then
    say "running as $PUID:$PGID"
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi
warn "uid $PUID can't write to $DATA; running as root instead so the app still works."
warn "To fix: give $PUID:$PGID write access to the folder on the host, or set PUID/PGID to its owner."
exec "$@"
