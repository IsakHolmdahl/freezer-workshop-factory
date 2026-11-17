#!/bin/sh
# Log in to GitHub Copilot when it is the provider, seed the gateway endpoints in
# the background, then run the given command.
set -eu

if [ "${MLFLOW_GATEWAY_PROVIDER:-}" = "github_copilot" ]; then
  export GITHUB_COPILOT_TOKEN_DIR="${GITHUB_COPILOT_TOKEN_DIR:-/var/lib/litellm-copilot}"
  mkdir -p "$GITHUB_COPILOT_TOKEN_DIR"
  if [ -s "$GITHUB_COPILOT_TOKEN_DIR/access-token" ]; then
    # Refresh api-key.json before gateway workers accept traffic.
    python /opt/mlflow-bootstrap/copilot_login.py || true
  else
    # No token yet: device login in the background (code in the log) so the
    # server starts and passes its healthcheck; make setup-copilot finishes it.
    python /opt/mlflow-bootstrap/copilot_login.py &
  fi
fi

python /opt/mlflow-bootstrap/seed_gateway.py &

exec "$@"
