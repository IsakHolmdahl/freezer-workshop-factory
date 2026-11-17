#!/bin/sh
# Run a Make workflow with container Python when no host interpreter is available.
set -eu

if ! command -v docker >/dev/null 2>&1; then
    printf '%s\n' 'Python is missing. Install Python 3.10+ or Docker, then run make again.' >&2
    exit 1
fi
printf '%s' 'Python is missing. Run this command using Docker python:3.12-alpine? [y/N] '
if ! read -r answer; then
    printf '\n%s\n' 'No response received. Install Python 3.10+ and retry.' >&2
    exit 1
fi
case "$answer" in
    y|Y|yes|YES) ;;
    *) printf '%s\n' 'Cancelled. Install Python 3.10+ and retry.'; exit 1 ;;
esac

root=$(pwd -P)
set -- docker run --rm -i --network host \
    --mount "type=bind,source=$root,target=$root" \
    --mount "type=bind,source=$HOME,target=$HOME" \
    --mount type=volume,source=asf-python312-alpine,target=/opt/asf-python \
    --workdir "$root" --env "HOME=$HOME" --env "ASF_UID=$(id -u)" --env "ASF_GID=$(id -g)" \
    --env "ASF_MAKE_TARGET=$1"
if [ -t 0 ] && [ -t 1 ]; then
    set -- "$@" -t
fi
if [ -S /var/run/docker.sock ]; then
    set -- "$@" --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
        --env "ASF_DOCKER_GID=$(stat -c %g /var/run/docker.sock 2>/dev/null || stat -f %g /var/run/docker.sock)"
fi
exec "$@" python:3.12-alpine sh -ec '
    apk add --no-cache make git bash docker-cli docker-cli-compose su-exec \
        go nodejs npm build-base openssh-client curl gcompat
    # Keep generated project/home files owned by the invoking host user.
    mkdir -p "/opt/asf-python/$ASF_UID"
    chown "$ASF_UID:$ASF_GID" "/opt/asf-python/$ASF_UID"
    runtime_gid="${ASF_DOCKER_GID:-$ASF_GID}"
    export ASF_PYTHON_VENV="/opt/asf-python/$ASF_UID/venv"
    export PATH="$HOME/.local/bin:$PATH"
    exec su-exec "$ASF_UID:$runtime_gid" make "$ASF_MAKE_TARGET" PYTHON=python3 \
        VENV_PYTHON="/opt/asf-python/$ASF_UID/venv/bin/python" \
        VENV_STAMP="/opt/asf-python/$ASF_UID/venv/.requirements-installed"
'
