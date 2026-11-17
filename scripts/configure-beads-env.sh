#!/bin/sh
# Compose startup hook: publish DOLT_URL and DOLT_DB_PASSWORD into the Scion Hub.
set -eu
[ -n "${DOLT_DB_PASSWORD:-}" ] || exit 0
attempt=0
until err=$(scion --global --yes --non-interactive hub env set --scope hub --as-needed DOLT_URL "$DOLT_URL" 2>&1); do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    printf '%s\n%s\n' 'Beads environment registration unavailable: Hub did not become ready.' "$err" >&2
    exit 1
  fi
  sleep 1
done
scion --global --yes --non-interactive hub env set --scope hub --as-needed --secret DOLT_DB_PASSWORD "$DOLT_DB_PASSWORD" >/dev/null
