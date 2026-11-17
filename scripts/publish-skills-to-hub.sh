#!/usr/bin/env bash
#
# publish-skills-to-hub.sh
#
# Publishes every skill under skills/ to the Scion Hub's skill bank as a
# *global* scope skill, then rewrites the `skills:` block of every
# templates/*/scion-agent.yaml so that skills we own locally are resolved
# through the Hub (skill://scion/global/<name>) instead of fetched directly
# from GitHub (gh://...) on every agent run.
#
# Requirements:
#   - `scion` CLI on PATH with a Hub selected. Runs with --global, so the
#     current directory need not be a scion project.
#   - The calling user must hold the `global-catalog-author` role (or
#     hub-admin) on the Hub, since publishing scope=global skills is
#     privileged. See pkg/hub/skill_global_authz_test.go in the scion repo.
#
# Usage:
#   ./scripts/publish-skills-to-hub.sh [--version X.Y.Z] [--skills-dir DIR]
#                                       [--templates-dir DIR] [--dry-run]
#                                       [--no-rewrite]
#
#   --version        Semver to publish for every skill (default: 1.0.0).
#                     If that version already exists for a skill on the Hub,
#                     the script skips publishing (idempotent re-runs) and
#                     still rewrites the template references.
#   --skills-dir      Directory containing one subdirectory per skill, each
#                      with a SKILL.md (default: skills, relative to repo
#                      root).
#   --templates-dir   Directory containing templates/*/scion-agent.yaml
#                      (default: templates, relative to repo root).
#   --dry-run         Print what would happen without calling `scion` or
#                      writing any files.
#   --no-rewrite      Publish skills but leave scion-agent.yaml files alone.
#
# The set of "local" skill names is derived from the subdirectory names in
# --skills-dir. Any `uri: "gh://.../<name>"` line in a scion-agent.yaml whose
# trailing path segment matches one of those names is rewritten to
# `uri: "skill://scion/global/<name>"`, regardless of which GitHub org/repo
# it previously pointed at. References to skills we do not own locally
# (e.g. gh://addyosmani/agent-skills/using-agent-skills, which has no local
# counterpart) are left untouched.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

VERSION="1.0.0"
SKILLS_DIR="${REPO_ROOT}/skills"
TEMPLATES_DIR="${REPO_ROOT}/templates"
DRY_RUN=0
REWRITE=1

usage() {
	sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
	exit 1
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--version)
		VERSION="$2"
		shift 2
		;;
	--skills-dir)
		SKILLS_DIR="$2"
		shift 2
		;;
	--templates-dir)
		TEMPLATES_DIR="$2"
		shift 2
		;;
	--dry-run)
		DRY_RUN=1
		shift
		;;
	--no-rewrite)
		REWRITE=0
		shift
		;;
	-h | --help)
		usage
		;;
	*)
		echo "Unknown argument: $1" >&2
		usage
		;;
	esac
done

if ! command -v scion >/dev/null 2>&1; then
	echo "error: 'scion' CLI not found on PATH" >&2
	exit 1
fi

if [[ ! -d "${SKILLS_DIR}" ]]; then
	echo "error: skills directory not found: ${SKILLS_DIR}" >&2
	exit 1
fi

# Never wait for input, and never wait forever: a stuck Hub call fails the
# skill with a message instead of freezing setup.
CALL_TIMEOUT="${SCION_CALL_TIMEOUT:-120}"
scion_call() {
	local timeout_cmd=()
	command -v timeout >/dev/null 2>&1 && timeout_cmd=(timeout "${CALL_TIMEOUT}")
	"${timeout_cmd[@]}" scion --global --yes --non-interactive "$@" </dev/null
}

run() {
	if [[ "${DRY_RUN}" -eq 1 ]]; then
		echo "+ $*"
	else
		"$@"
	fi
}

echo "Publishing skills from ${SKILLS_DIR} to the Hub as scope=global, version=${VERSION}"
echo

published_names=()
failed=0
skill_count=0
for skill_md in "${SKILLS_DIR}"/*/SKILL.md; do
	[[ -f "$skill_md" ]] && skill_count=$((skill_count + 1))
done
progress_total="${ASF_PUBLISH_TOTAL:-$skill_count}"
progress_offset="${ASF_PUBLISH_OFFSET:-0}"

skill_progress() {
	echo "Publish [$((progress_offset + ${#published_names[@]}))/${progress_total}] $1"
}

for skill_dir in "${SKILLS_DIR}"/*/; do
	[[ -d "${skill_dir}" ]] || continue
	name="$(basename "${skill_dir%/}")"

	if [[ ! -f "${skill_dir}/SKILL.md" ]]; then
		echo "skip ${name}: no SKILL.md found" >&2
		continue
	fi

	echo "== ${name} =="
	skill_progress "Publishing skill: ${name}"

	# Idempotency: if this exact version is already published, skip the
	# publish call but still record the name so template rewriting happens.
	if [[ "${DRY_RUN}" -ne 1 ]] && scion_call skills versions "${name}" --format json 2>/dev/null |
		grep -q "\"version\": *\"${VERSION}\""; then
		echo "  version ${VERSION} already published, skipping"
		published_names+=("${name}")
		skill_progress "Skill verified: ${name} (already published)"
		echo
		continue
	fi

	if run scion_call skills publish "${skill_dir%/}" --version "${VERSION}" --scope global; then
		published_names+=("${name}")
		skill_progress "Skill complete: ${name}"
	else
		echo "  failed to publish ${name}" >&2
		echo "  (each Hub call is limited to ${CALL_TIMEOUT}s; set SCION_CALL_TIMEOUT to change)" >&2
		failed=1
	fi
	echo
done

echo "Published/verified ${#published_names[@]} skill(s): ${published_names[*]-none}"

# Do not rewrite any references or report success after a partial publish.
if [[ "${failed}" -ne 0 ]]; then
	echo "error: one or more skills failed to publish; skipping template rewrite" >&2
	exit 1
fi

if [[ "${REWRITE}" -ne 1 ]]; then
	echo "Skipping scion-agent.yaml rewrite (--no-rewrite)."
	exit 0
fi

if [[ ! -d "${TEMPLATES_DIR}" ]]; then
	echo "warning: templates directory not found: ${TEMPLATES_DIR}, skipping rewrite" >&2
	exit 0
fi

echo
echo "Rewriting skill references in ${TEMPLATES_DIR}/*/scion-agent.yaml"
echo

for name in "${published_names[@]}"; do
	# Match `uri: "gh://<anything>/<name>"` (the skill name is the final path
	# segment) and rewrite it to the Hub-resolved global skill URI. Leaves
	# any version/qualifier suffix alone since local skills are unversioned
	# gh:// refs to begin with.
	pattern="uri: \"gh://[^\"]*/${name}\""
	replacement="uri: \"skill://scion/global/${name}\""

	matches=$(grep -rl -E "${pattern}" "${TEMPLATES_DIR}"/*/scion-agent.yaml 2>/dev/null || true)
	if [[ -z "${matches}" ]]; then
		continue
	fi

	while IFS= read -r file; do
		echo "  ${file}: gh://.../${name} -> skill://scion/global/${name}"
		if [[ "${DRY_RUN}" -ne 1 ]]; then
			sed -i -E "s#${pattern}#${replacement}#g" "${file}"
		fi
	done <<<"${matches}"
done

echo
echo "Done."
