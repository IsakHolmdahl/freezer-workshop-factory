#!/bin/sh
# ASF_SCION_EXTENSION
# Configure cross-project communication using the upstream CLI and jq.
set -eu
fail() { printf 'scion cross-project: %s\n' "$*" >&2; exit 1; }
usage() {
    printf '%s\n' 'Usage: scion cross-project [setup|apply|status|disable] [options]' \
        '  setup      Select project numbers (-1 for all), or use repeated --project SLUG' \
        '  apply      Apply the saved selection' '  status     Show Hub and project policies' \
        '  disable    Close managed projects; preserve policies owned by others' \
        '  --project SLUG   Participating project (setup only, repeatable)' \
        '  --inbound members|any   Inbound policy (setup only)' \
        '  --start          Start missing receiving coordinators (setup/apply)'
}
command=setup
case ${1-} in setup|apply|status|disable) command=$1; shift ;; esac
case ${1-} in -h|--help) usage; exit 0 ;; esac
command -v jq >/dev/null 2>&1 || fail 'jq is required; install jq with your package manager'
real=${SCION_REAL:?run through the scion shim}
home=${SCION_HOME:-$HOME/.scion}
config=$home/asf-cross-project-config.json
state=$home/asf-cross-project.json
call() { "$real" --non-interactive "$@"; }
query() { call "$@" --format json; }
clear_peers() {
    # Clearing an absent variable is already the desired state. Keep other
    # failures visible (authentication, connectivity, permissions, etc.).
    if output=$(call hub env clear "$@" ASF_PEER_PROJECTS 2>&1); then
        [ -z "$output" ] || printf '%s\n' "$output"
    else
        status=$?
        case $output in
            *'not_found: Resource not found'*) return 0 ;;
            *) printf '%s\n' "$output" >&2; return "$status" ;;
        esac
    fi
}
messaging() {
    # These upstream commands do not wire --format json to their JSON encoder.
    # Read their documented text fields, retaining revisions for CAS updates.
    output=$(call "$@") || return "$?"
    revision=
    value=
    field=
    while IFS= read -r line; do
        case $line in
            *'Messaging Settings (revision '*|*'Messaging Policy (revision '*)
                revision=${line##*revision }
                revision=${revision%%)*}
                ;;
        esac
        read -r key setting rest <<EOF
$line
EOF
        case $key in
            cross_project_messaging_enabled:) field=cross_project_messaging_enabled; value=$setting ;;
            cross_project_inbound:) field=crossProjectInbound; value=$setting ;;
        esac
    done <<EOF
$output
EOF
    case $revision in ''|*[!0-9]*) fail 'could not read messaging settings revision from Scion' ;; esac
    case $field:$value in
        cross_project_messaging_enabled:true|cross_project_messaging_enabled:false)
            jq -nc --argjson revision "$revision" --argjson value "$value" '{revision:$revision,cross_project_messaging_enabled:$value}' ;;
        crossProjectInbound:none|crossProjectInbound:members|crossProjectInbound:any)
            jq -nc --argjson revision "$revision" --arg value "$value" '{revision:$revision,crossProjectInbound:$value}' ;;
        *) fail 'could not read messaging policy from Scion' ;;
    esac
}
settings='{"peers":{},"inbound":"members"}'
if [ -f "$config" ]; then settings=$(jq -ce . "$config"); fi
peers=$(printf '%s' "$settings" | jq -ce '.peers')
inbound=$(printf '%s' "$settings" | jq -er '.inbound')
selected='[]'
explicit=false
start=false
policy_option=false
while [ "$#" -gt 0 ]; do
    case $1 in
        --project|--inbound)
            [ "$#" -ge 2 ] || fail "missing value for $1"
            case $1 in
                --project) selected=$(printf '%s' "$selected" | jq -c --arg slug "$2" '. + [$slug] | unique'); explicit=true ;;
                --inbound) inbound=$2; policy_option=true ;;
            esac
            shift 2 ;;
        --project=*) selected=$(printf '%s' "$selected" | jq -c --arg slug "${1#*=}" '. + [$slug] | unique'); explicit=true; shift ;;
        --inbound=*) inbound=${1#*=}; policy_option=true; shift ;;
        --start) start=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "unknown argument: $1" ;;
    esac
done
case $inbound in members|any) ;; *) fail 'inbound policy must be members or any' ;; esac
if [ "$command" != setup ] && { [ "$explicit" = true ] || [ "$policy_option" = true ]; }; then
    fail '--project and --inbound require setup'
fi
known=$(query hub projects)
known=$(printf '%s' "$known" | jq -ce '[.[] | select(.slug != "global")] | sort_by(.slug)')
managed='[]'
if [ -f "$state" ]; then managed=$(jq -ce '.projects // []' "$state"); fi
write_json() {
    mkdir -p "$home"
    temporary=$(mktemp "$1.XXXXXX")
    printf '%s\n' "$2" > "$temporary"
    mv "$temporary" "$1"
}
set_switch() {
    switch=$(messaging hub messaging get)
    current=$(printf '%s' "$switch" | jq -r '.cross_project_messaging_enabled // false')
    revision=$(printf '%s' "$switch" | jq -er '.revision')
    if [ "$current" != "$1" ]; then
        call hub messaging set "--cross-project-enabled=$1" --revision "$revision"
    fi
}
set_policy() {
    policy=$(messaging project messaging get --project "$1")
    current=$(printf '%s' "$policy" | jq -er '.crossProjectInbound')
    revision=$(printf '%s' "$policy" | jq -er '.revision')
    if [ "$current" != "$2" ]; then
        call project messaging set --project "$1" --policy "$2" --revision "$revision"
    fi
}
if [ "$command" = status ]; then
    call hub messaging get
    printf 'Saved selection: %s\n' "$settings"
    rows=$(printf '%s' "$known" | jq -r '.[] | [.slug,.id] | @tsv')
    while IFS="$(printf '\t')" read -r slug id; do
        [ -n "$slug" ] || continue
        policy=$(messaging project messaging get --project "$id")
        printf '%s: %s\n' "$slug" "$(printf '%s' "$policy" | jq -r '.crossProjectInbound')"
    done <<EOF
$rows
EOF
    exit 0
fi
if [ "$command" = setup ]; then
    [ "$(printf '%s' "$known" | jq length)" -ge 2 ] || fail 'at least two Hub projects are needed'
    if [ "$explicit" = false ]; then
        printf 'Available projects:\n'
        printf '%s' "$known" | jq -r --argjson peers "$peers" 'to_entries[] |
            "  \(.key + 1). \(.value.slug)\(if $peers[.value.slug] != null then " [selected]" else "" end)"'
        defaults=$(printf '%s' "$known" | jq -r --argjson peers "$peers" '[to_entries[] |
            select($peers[.value.slug] != null) | (.key + 1 | tostring)] | join(" ")')
        printf '%s\n' 'Select numbers, -1 for all, or prefix:NAME for slugs starting with NAME.' \
            'Add except NUMBERS to exclude entries (e.g. -1 except 2, or prefix:team- except 3).' \
            'Use except NUMBERS alone for all except those entries; none disables managed connections.'
        while :; do
            printf 'Project numbers [%s] (-1 for all, none to disable): ' "$defaults"
            IFS= read -r answer || exit 130
            answer=${answer:-$defaults}
            if selected=$(printf '%s' "$answer" | jq -Rce --argjson known "$known" '
                def numbers:
                    map(if test("^[0-9]+$") then tonumber else error("expected number") end) |
                    if all(.[]; . >= 1 and . <= ($known | length)) then . else error("out of range") end;
                gsub("^\\s+|\\s+$"; "") |
                if . == "none" or . == "" then []
                else [splits("[\\s,]+") | select(length > 0)] as $tokens |
                    ([$tokens | to_entries[] | select(.value == "except") | .key]) as $separators |
                    if ($separators | length) > 1 then error("duplicate except") else
                        ($separators[0] // ($tokens | length)) as $split |
                        $tokens[0:$split] as $include |
                        (if ($separators | length) == 1 then
                            $tokens[$split+1:] | if length == 0 then error("missing exclusions") else numbers end
                         else [] end) as $exclude |
                        (if ($include | length) == 0 then [range(1; ($known | length)+1)]
                         else [$include[] |
                            if . == "-1" then range(1; ($known | length)+1)
                            elif startswith("prefix:") then
                                ltrimstr("prefix:") as $prefix |
                                if $prefix == "" then error("empty prefix") else
                                    $known | to_entries[] | select(.value.slug | startswith($prefix)) | .key+1
                                end
                            else [.] | numbers | .[] end] end) as $indices |
                        ($indices - $exclude | unique) as $selected |
                        if ($selected | length) == 0 then error("no projects matched; use none to disable")
                        else [$selected[] | $known[. - 1].slug] end
                    end
                end' 2>/dev/null); then
                if [ "$(printf '%s' "$selected" | jq length)" -ne 1 ]; then break; fi
                printf 'Select at least two projects, or enter none.\n'
            else
                printf 'Invalid selection or no matches. Use numbers, -1, prefix:NAME, optionally except NUMBERS; none disables.\n'
            fi
        done
        if [ "$start" = false ] && [ "$selected" != '[]' ]; then
            printf 'Start missing receiving coordinators? [Y/n]: '
            IFS= read -r answer || exit 130
            case $answer in ''|y|Y|yes) start=true ;; esac
        fi
    fi
    [ "$(printf '%s' "$selected" | jq length)" -ne 1 ] || fail 'select at least two projects, or none'
    unknown=$(printf '%s' "$selected" | jq -c --argjson known "$known" '. - [$known[].slug]')
    [ "$unknown" = '[]' ] || fail "unknown projects: $unknown"
    peers=$(printf '%s' "$selected" | jq -c --argjson previous "$peers" 'map({key:.,value:($previous[.] // "coordinator")}) | from_entries')
    settings=$(jq -nc --argjson peers "$peers" --arg inbound "$inbound" '{peers:$peers,inbound:$inbound}')
    write_json "$config" "$settings"
fi
if [ "$command" = disable ]; then peers='{}'; fi
unknown=$(printf '%s' "$peers" | jq -c --argjson known "$known" 'keys - [$known[].slug]')
[ "$unknown" = '[]' ] || fail "unknown saved projects: $unknown"
selected=$(printf '%s' "$peers" | jq -c 'keys')
# Record the union first: failed partial updates remain managed and can be retried.
pending=$(jq -nc --argjson old "$managed" --argjson new "$selected" '{projects:($old+$new|unique)}')
write_json "$state" "$pending"
if [ "$selected" != '[]' ]; then set_switch true; fi
rows=$(printf '%s' "$known" | jq -r '.[] | [.slug,.id] | @tsv')
while IFS="$(printf '\t')" read -r slug id; do
    [ -n "$slug" ] || continue
    receiver=$(printf '%s' "$peers" | jq -r --arg slug "$slug" '.[$slug] // empty')
    if [ -n "$receiver" ]; then
        set_policy "$id" "$inbound"
        value=$(printf '%s' "$peers" | jq -r 'to_entries | sort_by(.key) | map("\(.key)=\(.value)") | join(",")')
        call hub env set "--project=$id" --always ASF_PEER_PROJECTS "$value"
        agents=$(query list --project "$id")
        if ! printf '%s' "$agents" | jq -e --arg receiver "$receiver" 'any(.[]; (.slug // .name) == $receiver)' >/dev/null; then
            if [ "$start" = true ]; then
                call start "$receiver" --project "$id" --type coordinator --message-mode hub \
                    'Wait for delegated work from coordinators in other projects (cross-project-delegation skill).'
            else
                printf '%s: receiver %s missing; use apply --start\n' "$slug" "$receiver"
            fi
        fi
    elif printf '%s' "$managed" | jq -e --arg slug "$slug" 'index($slug) != null' >/dev/null; then
        set_policy "$id" none
        clear_peers "--project=$id"
    fi
done <<EOF
$rows
EOF
if [ "$selected" = '[]' ]; then
    others=false
    while IFS="$(printf '\t')" read -r slug id; do
        [ -n "$slug" ] || continue
        policy=$(messaging project messaging get --project "$id")
        current=$(printf '%s' "$policy" | jq -er '.crossProjectInbound')
        if [ "$current" != none ]; then others=true; fi
    done <<EOF
$rows
EOF
    if [ "$others" = false ]; then set_switch false; fi
fi
write_json "$state" "$(jq -nc --argjson projects "$selected" '{projects:$projects}')"
clear_peers --scope hub
printf 'Cross-project communication applied. New coordinators receive the updated peer list.\n'
