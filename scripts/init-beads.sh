#!/usr/bin/env bash
#
# init-beads.sh
#
# One-time (idempotent) setup of the shared beads workspace used by all agents.
# Agents never run `bd init`: they find the workspace through BEADS_DIR and
# connect to the shared Dolt server through BEADS_DOLT_SERVER_* (both preset in
# the harness configs).
#
# Usage:
#   ./init-beads.sh [--dir DIR] [--host HOST] [--port PORT] [--prefix PFX]
#
# Defaults match compose.yaml and harness-configs/*/config.yaml.

set -euo pipefail

DIR="${BEADS_DIR:-/scion-volumes/scratchpad/beads/.beads}"
HOST="${BEADS_DOLT_SERVER_HOST:-127.0.0.1}"
PORT="${BEADS_DOLT_SERVER_PORT:-3307}"
PREFIX="sf"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)    DIR="$2"; shift 2 ;;
    --host)   HOST="$2"; shift 2 ;;
    --port)   PORT="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

command -v bd >/dev/null || { echo "bd not found on PATH" >&2; exit 1; }

mkdir -p "$(dirname "$DIR")"
export BEADS_DIR="$DIR" BEADS_DOLT_SERVER_MODE=1 \
       BEADS_DOLT_SERVER_HOST="$HOST" BEADS_DOLT_SERVER_PORT="$PORT"

# --external: the server is run by docker compose, bd must not start its own.
# --stealth: no git hooks or git operations (the workspace is not a git repo).
bd init --server --external --init-if-missing --non-interactive --quiet --stealth \
  --prefix "$PREFIX" --server-host "$HOST" --server-port "$PORT"

bd list --limit 1 >/dev/null
echo "beads workspace ready: BEADS_DIR=$DIR server=$HOST:$PORT prefix=$PREFIX"
