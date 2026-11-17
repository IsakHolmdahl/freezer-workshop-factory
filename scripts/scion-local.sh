#!/bin/sh
# ASF_SCION_EXTENSION
# Local-folder projects, without cloning a Git remote. Requires jq and a SHA256 tool.
set -eu
fail() { printf 'scion local: %s\n' "$*" >&2; exit 1; }
usage() {
    printf '%s\n' 'Usage: scion local init [--slug NAME] [--broker NAME]' \
        '       scion local start AGENT [Scion flags and task]' \
        '       scion local list|logs|look|attach|stop|resume|delete [Scion arguments]' \
        'Run inside the folder. Agents edit it directly; mount it at the same path in the broker.'
}
case ${1-} in ''|-h|--help) usage; exit 0 ;; esac
command -v jq >/dev/null 2>&1 || fail 'jq is required; install jq with your package manager'
folder=$(pwd -P)
if command -v sha256sum >/dev/null 2>&1; then
    hash=$(printf '%s' "$folder" | sha256sum)
else
    hash=$(printf '%s' "$folder" | shasum -a 256)
fi
saved=$HOME/.config/scion-local/projects/${hash%% *}.json
real=${SCION_REAL:?run through the scion shim}
call() { (cd "$HOME" && "$real" "$@"); }
query() { call "$@" --format json; }
action=$1
shift
if [ "$action" = init ]; then
    slug=
    broker=hosted-broker
    while [ "$#" -gt 0 ]; do
        case $1 in
            --slug|--broker)
                [ "$#" -ge 2 ] || fail "missing value for $1"
                case $1 in --slug) slug=$2 ;; --broker) broker=$2 ;; esac
                shift 2 ;;
            --slug=*) slug=${1#*=}; shift ;;
            --broker=*) broker=${1#*=}; shift ;;
            -h|--help) usage; exit 0 ;;
            *) fail "unknown init argument: $1" ;;
        esac
    done
    if [ -z "$slug" ] && [ -f "$saved" ]; then slug=$(jq -r '.slug // empty' "$saved"); fi
    if [ -z "$slug" ]; then
        name=$(basename "$folder")
        slug=$(printf '%s' "$name" | LC_ALL=C tr '[:upper:]' '[:lower:]' | LC_ALL=C tr -cs 'a-z0-9' '-' | sed 's/^-//;s/-$//')-local
    fi
    brokers=$(query hub brokers)
    match=$(printf '%s' "$brokers" | jq -ce --arg broker "$broker" \
        '[.[] | select(.id == $broker or .name == $broker or .slug == $broker)] | select(length == 1) | .[0] | select(.status == "online")') \
        || fail "expected one online broker matching $broker"
    broker_id=$(printf '%s' "$match" | jq -er '.id')
    known=$(query hub projects)
    matches=$(printf '%s' "$known" | jq -c --arg slug "$slug" '[.[] | select(.slug == $slug)]')
    count=$(printf '%s' "$matches" | jq 'length')
    [ "$count" -le 1 ] || fail "ambiguous project slug: $slug"
    if [ "$count" -eq 1 ]; then
        project=$(printf '%s' "$matches" | jq -c '.[0]')
        printf '%s' "$project" | jq -e '(.gitRemote // "") == "" and ((.labels["scion.dev/workspace-mode"] // "shared") == "shared")' >/dev/null \
            || fail "$slug is Git-based or is not a shared-directory project; choose another --slug"
        id=$(printf '%s' "$project" | jq -er '.id')
        info=$(query hub project info "$id")
        printf '%s' "$info" | jq -e --arg folder "$folder" \
            'all(.providers[]?; (.localPath // "") as $p | $p == "" or ($p | sub("/\\.scion/?$"; "")) == $folder)' >/dev/null \
            || fail "$slug is already mapped to another folder"
    else
        project=$(query hub project create --name "$(basename "$folder")" --slug "$slug" --workspace-mode shared)
        id=$(printf '%s' "$project" | jq -er '.id')
    fi
    mkdir -p "$folder/.scion/agents"
    call runtime-broker provide --project "$id" --broker "$broker_id" --path "$folder" --make-default -y
    mkdir -p "$(dirname "$saved")"
    tmp=$(mktemp "$saved.XXXXXX")
    trap 'rm -f "$tmp"' EXIT HUP INT TERM
    jq -n --arg id "$id" --arg slug "$slug" --arg folder "$folder" '{id:$id,slug:$slug,folder:$folder}' > "$tmp"
    mv "$tmp" "$saved"
    printf 'Ready: %s → %s\nAgents edit this folder directly; no Git remote is cloned.\n' "$slug" "$folder"
    exit 0
fi
case $action in start|list|logs|look|attach|stop|resume|delete) ;; *) fail "unsupported command: $action" ;; esac
[ -f "$saved" ] || fail 'run scion local init in this folder first'
for arg do
    case $arg in
        --project|-g|--workspace|-w|--global|--hub|--project=*|--workspace=*|--hub=*|-g?*|-w?*)
            fail 'project, Hub and workspace overrides are not supported' ;;
    esac
done
id=$(jq -er '.id' "$saved")
if [ "$action" = start ]; then set -- --workspace "$folder" "$@"; fi
cd "$HOME"
exec "$real" --project "$id" "$action" "$@"
