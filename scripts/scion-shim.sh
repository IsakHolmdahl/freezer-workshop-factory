#!/bin/sh
# ASF_SCION_SHIM
# Dispatch ASF extensions and exec the unmodified upstream CLI otherwise.

# Resolve PATH invocations and symlink aliases to the installed shim.
executable=$0
case $executable in
    */*) ;;
    *) executable=$(command -v "$executable") || exit 127 ;;
esac
while :; do
    directory=$(CDPATH= cd -P -- "$(dirname -- "$executable")" && pwd) || exit 1
    executable=$directory/$(basename -- "$executable")
    [ -L "$executable" ] || break
    link=$(readlink "$executable") || exit 1
    case $link in
        /*) executable=$link ;;
        *) executable=$directory/$link ;;
    esac
done

# Cobra's generated Bash/Zsh/Fish scripts all use this completion protocol.
complete_words() {
    for word in $1; do
        case $word in
            "$2"*)
                description=
                case $word in
                    local) description='Manage local-folder projects without cloning' ;;
                    cross-project) description='Configure cross-project communication' ;;
                    sessions) description='List running agent terminal sessions' ;;
                    watch) description='View agents in tmux panes and tabs' ;;
                    --all) description='Include agents across all projects' ;;
                    --json) description='Output session candidates as JSON' ;;
                    --panes) description='Maximum panes per window (default 4)' ;;
                    --session) description='Viewer tmux session name' ;;
                    --detach) description='Create viewer without attaching' ;;
                    [1-8]) description='Panes per tmux window' ;;
                    setup) description='Select projects and configure communication' ;;
                    apply) description='Apply saved communication settings' ;;
                    status) description='Show cross-project messaging policies' ;;
                    disable) description='Disable communication for managed projects' ;;
                    init) description='Initialize a local-folder project' ;;
                    start) description='Start an agent in the local folder' ;;
                    list) description='List agents in the local-folder project' ;;
                    logs) description='Show agent logs' ;;
                    look) description='Inspect an agent terminal' ;;
                    attach) description='Attach to an agent terminal' ;;
                    stop) description='Stop an agent' ;;
                    resume) description='Resume an agent' ;;
                    delete) description='Delete an agent' ;;
                    --project) description='Participating project slug (repeatable)' ;;
                    --inbound) description='Inbound messaging policy' ;;
                    --start) description='Start missing receiving coordinators' ;;
                    --slug) description='Local project slug' ;;
                    --broker) description='Runtime broker name or ID' ;;
                    --help) description='Show help for this command' ;;
                    members) description='Accept messages from project members' ;;
                    any) description='Accept messages from any eligible agent on the Hub' ;;
                esac
                if [ "$protocol" = __complete ] && [ -n "$description" ]; then
                    printf '%s\t%s\n' "$word" "$description"
                else
                    printf '%s\n' "$word"
                fi
                ;;
        esac
    done
}

case ${1-} in
    __complete|__completeNoDesc)
        protocol=$1
        if [ "$#" -eq 2 ]; then
            # Keep upstream candidates and its final directive, adding our commands.
            output=$("$executable-real" "$@") || exit "$?"
            directive=:4
            while IFS= read -r line; do
                case $line in
                    :[0-9]*) directive=$line ;;
                    *) printf '%s\n' "$line" ;;
                esac
            done <<EOF
$output
EOF
            complete_words 'local cross-project sessions watch' "$2"
            printf '%s\n' "$directive"
            exit 0
        fi
        case ${2-} in
            sessions|watch)
                previous=
                prefix=
                for argument do previous=$prefix; prefix=$argument; done
                case $previous in
                    --panes) complete_words '1 2 3 4 5 6 7 8' "$prefix" ;;
                    --project|--session) : ;;
                    *)
                        if [ "$2" = sessions ]; then
                            complete_words '--all --project --json --help' "$prefix"
                        else
                            complete_words '--all --project --panes --session --detach --help' "$prefix"
                        fi
                        ;;
                esac
                printf '%s\n' ':4'
                exit 0
                ;;
            cross-project)
                if [ "$#" -eq 3 ]; then
                    complete_words 'setup apply status disable --help' "$3"
                else
                    previous=
                    prefix=
                    for argument do
                        previous=$prefix
                        prefix=$argument
                    done
                    case $previous in
                        --inbound) complete_words 'members any' "$prefix" ;;
                        --project) : ;;
                        *) complete_words '--project --inbound --start --help' "$prefix" ;;
                    esac
                fi
                printf '%s\n' ':4'
                exit 0
                ;;
            local)
                shift 2
                if [ "$#" -eq 1 ]; then
                    complete_words 'init start list logs look attach stop resume delete --help' "$1"
                    printf '%s\n' ':4'
                    exit 0
                fi
                if [ "${1-}" = init ]; then
                    for prefix do :; done
                    complete_words '--slug --broker --help' "$prefix"
                    printf '%s\n' ':4'
                    exit 0
                fi
                exec "$executable-real" "$protocol" "$@"
                ;;
        esac
        ;;
esac

if [ "$#" -eq 1 ] && [ "$1" = completion ]; then
    shell=${SHELL##*/}
    case $shell in
        bash|zsh|fish) set -- completion "$shell" ;;
        *)
            printf '%s\n' 'scion: cannot detect a supported shell from $SHELL; use scion completion bash|zsh|fish' >&2
            exit 2
            ;;
    esac
fi

case ${1-} in
    sessions|watch)
        SCION_REAL=$executable-real
        export SCION_REAL
        exec /bin/sh "$executable-sessions.sh" "$@"
        ;;
    cross-project|local)
        extension=$1
        shift
        SCION_REAL=$executable-real
        export SCION_REAL
        exec /bin/sh "$executable-$extension.sh" "$@"
        ;;
    *) exec "$executable-real" "$@" ;;
esac
