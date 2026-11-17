#!/bin/sh
# Seed the gateway endpoints in the background, then run the given command.
set -eu

python /opt/mlflow-bootstrap/seed_gateway.py &

exec "$@"
