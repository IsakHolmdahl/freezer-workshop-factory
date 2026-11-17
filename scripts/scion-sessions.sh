#!/bin/sh
# ASF_SCION_EXTENSION
# List agent terminal sessions or view them in grouped tmux panes/windows.
set -eu
fail() { printf 'scion %s: %s\n' "$mode" "$*" >&2; exit 1; }
mode=${1:-sessions}
shift
usage() {
    printf '%s\n' 'Usage: scion sessions [--all | --project PROJECT] [--json]' \
        '       scion watch [AGENT ...] [--all | --project PROJECT] [options]' \
        '  --panes N       Panes per window (1-8; default 4)' \
        '  --session NAME  Viewer session name (default scion-watch-PID)' \
        '  --detach        Create viewer without attaching' \
        'Viewer prefix: Ctrl-a. Ctrl-a d detaches; Ctrl-a n/p switches windows.'
}
project=
all=false
json=false
panes=4
session=scion-watch-$$
detach=false
names='[]'
case ${1-} in -h|--help) usage; exit 0 ;; esac
command -v jq >/dev/null 2>&1 || fail 'jq is required; run setup or install jq'
while [ "$#" -gt 0 ]; do
    case $1 in
        --project|--panes|--session)
            [ "$#" -ge 2 ] || fail "missing value for $1"
            case $1 in --project) project=$2 ;; --panes) panes=$2 ;; --session) session=$2 ;; esac
            shift 2 ;;
        --project=*) project=${1#*=}; shift ;;
        --panes=*) panes=${1#*=}; shift ;;
        --session=*) session=${1#*=}; shift ;;
        --all) all=true; shift ;;
        --json) json=true; shift ;;
        --detach) detach=true; shift ;;
        -h|--help) usage; exit 0 ;;
        --) shift; for name do names=$(printf '%s' "$names" | jq -c --arg name "$name" '. + [$name] | unique'); done; break ;;
        -*) fail "unknown option: $1" ;;
        *) names=$(printf '%s' "$names" | jq -c --arg name "$1" '. + [$name] | unique'); shift ;;
    esac
done
[ "$all" = false ] || [ -z "$project" ] || fail '--all and --project are mutually exclusive'
case $panes in 1|2|3|4|5|6|7|8) ;; *) fail '--panes must be an integer from 1 to 8' ;; esac
case $session in ''|*[!a-zA-Z0-9_-]*) fail '--session must contain only letters, digits, hyphens and underscores' ;; esac
real=${SCION_REAL:?run through the scion shim}
set -- list --phase running --format json
if [ "$all" = true ]; then set -- "$@" --all; fi
if [ -n "$project" ]; then set -- "$@" --project "$project"; fi
agents=$("$real" "$@")
agents=$(printf '%s' "$agents" | jq -ce 'if type == "array" then . else error("expected agent list") end')
if [ "$names" != '[]' ]; then
    missing=$(printf '%s' "$agents" | jq -c --argjson names "$names" '$names - [.[] | (.slug // .name), .name]')
    [ "$missing" = '[]' ] || fail "agents not running or not found in selected scope: $missing"
    agents=$(printf '%s' "$agents" | jq -c --argjson names "$names" '[.[] | select((.slug // .name) as $slug | .name as $name | ($names | index($slug) != null or index($name) != null))]')
fi
agents=$(printf '%s' "$agents" | jq -c 'sort_by(.project, .slug // .name)')
if [ "$mode" = sessions ]; then
    if [ "$json" = true ]; then printf '%s\n' "$agents"; exit 0; fi
    printf 'PROJECT\tAGENT\tPHASE\tACTIVITY\n'
    printf '%s' "$agents" | jq -r '.[] | [.project // .projectId // "-", .slug // .name, .phase // "running", .activity // "-"] | @tsv'
    printf '\nRunning agents are session candidates; attach availability is checked by scion attach.\n'
    exit 0
fi
[ "$json" = false ] || fail '--json is only supported by sessions'
[ "$agents" != '[]' ] || fail 'no running agents in the selected scope'
command -v tmux >/dev/null 2>&1 || fail 'tmux is required on the host (Ubuntu: sudo apt install tmux; macOS: brew install tmux)'
if [ "$detach" = false ] && { [ ! -t 0 ] || [ ! -t 1 ]; }; then
    fail 'an interactive terminal is required; use --detach to create the viewer without attaching'
fi
if tmux has-session -t "=$session" 2>/dev/null; then fail "tmux session already exists: $session"; fi
# Encode each argument using jq's established shell escaping, not eval or send-keys.
commands=$(printf '%s' "$agents" | jq -r --arg real "$real" --arg project "$project" --argjson all "$all" '
    .[] | (.projectId // (if $project != "" then $project else .project end)) as $scope |
    if $all and ($scope == null or $scope == "") then error("agent has no project identity") else
    (["exec", $real] + (if $scope != null and $scope != "" then ["--project", $scope] else [] end) +
     ["attach", (.slug // .name)]) | @sh end')
created=false
complete=false
cleanup() {
    if [ "$created" = true ] && [ "$complete" = false ]; then tmux kill-session -t "$session" 2>/dev/null || :; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' HUP TERM
count=0
window=
while IFS= read -r command; do
    [ -n "$command" ] || continue
    if [ "$count" -eq 0 ]; then
        window=$(tmux new-session -d -P -F '#{window_id}' -s "$session" -n agents-1 -x "${COLUMNS:-160}" -y "${LINES:-48}" /bin/sh -c "$command")
        created=true
        tmux set-option -t "$session" prefix C-a
        tmux set-option -t "$session" mouse on
        tmux set-option -t "$session" pane-border-status top
        tmux set-option -t "$session" pane-border-format '#{pane_index}: #{pane_title}'
    elif [ "$((count % panes))" -eq 0 ]; then
        window=$(tmux new-window -d -P -F '#{window_id}' -t "$session:" -n "agents-$((count / panes + 1))" /bin/sh -c "$command")
    else
        tmux split-window -d -t "$window" /bin/sh -c "$command"
    fi
    tmux select-layout -t "$window" tiled >/dev/null
    count=$((count + 1))
done <<EOF
$commands
EOF
complete=true
printf 'Viewer %s: %s agents, up to %s panes per window. Ctrl-a d detaches; Ctrl-a n/p switches tabs.\n' "$session" "$count" "$panes"
if [ "$detach" = true ]; then exit 0; fi
if [ -n "${TMUX:-}" ]; then exec tmux switch-client -t "$session"; fi
exec tmux attach-session -t "$session"
