---
name: beads-hygiene
description: >-
  How `backlog-curator` analyzes `friction`, `agent-failed`, `rework`, and
  `user-interjection` beads, updates `bd memories` (`bd remember`), prunes stale
  or orphaned `in_progress` issues, compacts closed beads, and writes a concise
  retrospective on `/scion-volumes/scratchpad/`.
---

# Beads Hygiene & Retrospective Curation

Never edit repository source files or switch branches.

## 1. Analyze Friction & Failed Attempts

1. Query uncurated user-interjection, friction, and failure beads:
   ```bash
   bd list -l user-interjection --status all --json
   bd list -l friction --status all --json
   bd list -l agent-failed --status all --json
   bd list -l rework --status all --json
   ```
2. For each uncurated bead (`curated` not in `labels`), read `bd show <id> --json` (and any scratchpad report referenced in its `close_reason` or `notes`; treat `user-interjection` as highest priority):
   - **Real tooling/build/env blocker:** Save or update a 1-line blocker workaround in Dolt so future agents do not repeat the same broken attempt:
     ```bash
     bd remember "<1-line: do X instead of Y>" --key <blocker-topic>
     ```
   - **Role template or skill gap:** Record a 1-line recommendation naming the exact `templates/<role>/agents.md` or `skills/<skill>/SKILL.md` file to adjust.
   - Mark the bead curated so it is not re-analyzed next time:
     ```bash
     bd update <id> --add-label curated
     ```

## 2. Audit `bd memories` & Open Backlog

1. Run `bd memories` and check each entry against the live repo (`pom.xml`, `package.json`, `pyproject.toml`, `docs/`, `.docs/`, scripts). Delete (`bd forget <key>`) any obsolete entry or anything that is general codebase trivia/architecture rather than a real tooling/build blocker. Keep total memories under 10 single-line blocker rules.
2. Check `bd list --status=open --json`, `bd list --status=in_progress --json`, `bd stale --json`, and `bd duplicates --json`:
   - Close issues already fixed in `git log -n 50 --oneline` (`bd close <id> -r "Resolved in <sha>"`).
   - Merge duplicates (`bd duplicate <dup-id> --of <canonical-id>`).
   - Reset orphaned `in_progress` beads whose `assignee` worker container is absent from `scion list --non-interactive` (`bd update <id> -s open --assignee ""`).
3. Compact verbose closed beads older than 30 days (`bd admin compact --analyze --json --limit 20` → `bd admin compact --apply` → `bd admin compact --dolt`).

## 3. Report & Close

1. Write your retrospective report to `/scion-volumes/scratchpad/projects/<slug>/${BEADS_ISSUE_ID:-curator}-report.md` with:
   - **What went wrong & why** (grouped by pattern).
   - **Auto-applied repo memories (`bd remember --key`)** and pruned memories (`bd forget`).
   - **Recommended template/skill fixes** for the human operator.
2. Close your curator bead:
   ```bash
   bd close "${BEADS_ISSUE_ID:-<id>}" -r "Curated friction beads; report at /scion-volumes/scratchpad/projects/<slug>/${BEADS_ISSUE_ID:-curator}-report.md"
   ```
3. Message your dispatcher with `--body-file` pointing to the report and run `sciontool status task_completed`.
