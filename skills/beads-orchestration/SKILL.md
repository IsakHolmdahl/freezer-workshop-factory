---
name: beads-orchestration
description: >-
  Lightweight dispatcher protocol for `coordinator` and `eng-manager`: Beads (`bd`)
  for the task DAG, ready queue, status (`--claim` / `close`), and friction labels;
  `/scion-volumes/scratchpad/` for briefs, designs, and reports; parallel multi-agent
  feature waves; `--harness-config` selection; and mandatory `backlog-curator` trigger.
---

# Beads Orchestration (Dispatcher Protocol)

**Keep Beads lightweight:** Use `bd` only for the **task dependency graph (`create`, `dep add`, `ready`), task status (`--claim`, `close`), friction labels (`-l friction`, `-l rework`, `-l agent-failed`, `-l user-interjection`), and resolved tooling/build blockers (`bd memories`, `bd remember`)**.
Put all rich Markdown documents — **briefs, design docs, research notes, and review/QA reports** — as normal files on the shared scratchpad (`/scion-volumes/scratchpad/projects/<slug>/`) using your file `Write`/`Read` tools (never pipe multi-line Markdown into `bd` flags).

Re-read `bd list --parent <epic>`, `scion list --non-interactive`, and your `<agent-name>-state.md` on `/scion-volumes/scratchpad/` on every wake-up.

**Never run `bd init`, `bd doctor --fix` or `bd dolt start`:** the project database on the shared Dolt server is created and connected for you at start. If `bd` reports no database or a connection error, do not repair it: run `sciontool status blocked "Beads unavailable: <error>"` and tell your human contact.


## 1. Epics, Integration Branches, Mandatory Feature Teams & Scratchpad Briefs

1. **Check Blocker Memories (`bd memories`):** Check the `bd memories` block already injected at the top of your `AGENTS.md` for known build/environment pitfalls and include relevant warnings in worker briefs. Use `bd remember "<1-line: do X instead of Y>" --key <blocker-topic>` **only** for real build/test/environment blockers so future agents do not repeat the same broken attempt (never for codebase facts or general notes).
2. **Create the Epic & Ready-to-Push Target Branch:**
   - Choose the branch prefix from the work type: **`<feature|bugfix|improvement|chore>/<ticket-id>-<short-summary>`** (use the Jira ticket ID if provided in the prompt, e.g. `feature/PROJ-123-oauth-login`, `bugfix/PROJ-456-null-token`; otherwise use the epic/bead ID as `<ticket-id>`, e.g. `feature/${EPIC}-oauth-login`).
   ```bash
   EPIC=$(bd create "<project>" -t epic -l project:<slug> --silent)
   git switch -c "<feature|bugfix|improvement|chore>/<ticket-id>-<short-summary>"
   ```
   Workers run on `--branch work/<bead-id>-<short-topic>`; as each `work/*` branch passes review/QA, merge it into `<feature|bugfix|improvement|chore>/<ticket-id>-<short-summary>` (`git merge --no-ff work/<bead-id>-<short-topic>`) and run the build/test suite on the combined branch.
3. **Mandatory Feature Team & Task Decomposition (NEVER delegate a feature to a single developer):**
   - **Feature implementations ALWAYS use a multi-agent team, never a single `developer`:** Decompose every feature into multiple task beads across roles — e.g. `architect` / `investigator` (when design or discovery is needed), **2+ parallel `developer` beads** split across disjoint concerns/layers/components (or `developer` + `test-engineer` + `doc-writer`), followed by `code-reviewer` and `qa-tester`.
   - Create lightweight task beads in the DAG (`T1=$(bd create "<slice title>" -t task --parent "$EPIC" --silent)`).
   - Use `bd dep add <task> --blocked-by <other>` **only** when one task genuinely imports or depends on another's new contract. Partition tasks onto **disjoint file paths** so they can run simultaneously in the same wave.
4. **Write Briefs as Normal Files on the Shared Scratchpad:**
   - Write each worker's brief to `/scion-volumes/scratchpad/projects/<slug>/briefs/<worker-name>.md` using your `Write` tool (never `/workspace/.scratch/`).
   - Include: Bead ID, `Dispatcher: <your-name>`, `Completion recipient: agent:<your-name>`, `Direct contact for product questions: <human>`, `Jira ticket: <JIRA-TICKET-ID>` (when present in the prompt), disjoint `Owned paths:`, acceptance criteria, the mandatory feature documentation path (`documentation/`, `docs/`, or `.docs/`), and the commit message rule: **`<JIRA-TICKET-ID> <conventional commit message>`** (when a Jira ticket ID is provided, else `<conventional commit message>`).
5. **Select the Right Harness (`--harness-config`) & Launch Parallel Waves:**
   - Always pass `--harness-config asf-opencode` for workers that build, test, or inspect code.
   - Launch **all** `bd ready --parent "$EPIC"` tasks in parallel in the same turn, named **`<bead-id>-<short-topic>-<role>`** (the worker claims its own bead on startup via `bd update "$BEADS_ISSUE_ID" --claim` — no manual `agent-live` labels needed):
     ```bash
     scion start <worker-name> --non-interactive --type <template> \
       --harness-config asf-opencode --branch work/<bead-id>-<short-topic> \
       "Execute bead <task>: read /scion-volumes/scratchpad/projects/<slug>/briefs/<worker-name>.md"
     ```
6. **Git Push / Missing `GITHUB_TOKEN` Fallback, Branch Naming & Commit Message Format (Always Prepare a Branch Ready to Push):**
   - **Target Branch Name:** The final branch ready to be pushed **MUST** be named **`<feature|bugfix|improvement|chore>/<ticket-id>-<short-summary>`** (using the Jira ticket ID when present in the prompt, or the bead/epic ID otherwise).
   - **Commit Author & Message Format:** Never change the git commit author (`user.name` / `user.email` / `--author`). Every commit on worker and target branches must use the format **`<JIRA-TICKET-ID> <conventional commit message>`** (e.g., `PROJ-123 feat(api): add user endpoint`) whenever a Jira ticket ID is included in the prompt, or `<conventional commit message>` if none was provided, with the trailer **`Co-authored-by: factory-agent (ai-software-factory) <scion-agents@example.com>`**.
   - If workers or you cannot `git push` to `origin` because `GITHUB_TOKEN` / remote git credentials are missing, expired, or denied, **do NOT fail or block the epic**.
   - Keep all verified commits merged cleanly onto the local branch `<feature|bugfix|improvement|chore>/<ticket-id>-<short-summary>` in the shared repository, verify the build and tests pass, and report the exact branch name, head commit SHA, and `git push -u origin <feature|bugfix|improvement|chore>/<ticket-id>-<short-summary>` command to the user.
7. **Mandatory Scheduled Reminder Before Blocking (`scion schedule create --in 4m`):**
   - **Every time** you are about to wait on running workers (`sciontool status blocked`), create a single scheduled wake-up reminder using `--in 4m` (which stays inside the 5-minute provider prompt-cache TTL) and `--message` (never `--prompt`):
     ```bash
     scion schedule create --in 4m --agent "$SCION_AGENT_NAME" --message "Reminder: check active wave progress and inspect any stalled workers"
     sciontool status blocked "Waiting on wave for epic $EPIC"
     ```
   - Pending reminders for `$SCION_AGENT_NAME` are automatically canceled by the harness on wake-up and when you run `sciontool status task_completed`. Never schedule a new reminder unless you are about to call `sciontool status blocked`.

## 2. Failure, Friction & Mandatory Curation Protocol

- **Reconcile active work via `bd list --status=in_progress` and `scion list --non-interactive`:** Workers claim their bead on start (`status=in_progress`, `assignee=<worker-name>`) and close it on completion (`bd close <id> -r "..."`).
- **Never reuse a worker; remove it once its artifact is handed off:** Remove a worker only after its artifact is verified (developer: `git log -1 --oneline <branch>` matches the SHA in its handoff; others: the report file on `/scion-volumes/scratchpad/` exists and is non-empty), using `scion delete <worker> --non-interactive --preserve-branch`. Plain `scion delete` deletes the agent's git branch and commits!
- **Rework (`-l rework`):** Never reuse a developer. Create a `Fix:` bead (`bd create "Fix: ..." -t bug --parent "$EPIC" --deps discovered-from:<task> -l rework -l friction --silent`), write the fix brief to `/scion-volumes/scratchpad/projects/<slug>/briefs/`, and start a fresh `<fix-bead-id>-<short-topic>-fix<N>-developer` on `--branch work/<bead-id>-<short-topic>`.
- **Failed/stalled worker (`-l agent-failed`):** First check with `scion look <worker>` and try `scion message <worker> "continue"` for transient stalls. If the container crashed or cannot recover, delete it (`scion delete --preserve-branch`) and reopen in one call:
  `bd update <task> -s open --assignee "" -l agent-failed -l friction --append-notes "ATTEMPT_FAILED: <reason>"`
- **Any build/test/tool/coordination snag (`-l friction`):** Whenever a worker or dispatcher hits a build, test, environment, or handoff snag (even if worked around), ensure the bead is labeled `-l friction` (`bd update <id> --add-label friction --append-notes "FRICTION: <cause and fix>"`).
- **Human interjection (`-l user-interjection`):** Whenever the user steps in to fix, unblock, or redirect an agent, record it immediately on the epic or active task bead:
  `bd update <id> -l user-interjection -l friction --append-notes "USER_INTERJECTION: <what the user fixed>"`
- **3-strike flow-around:** After `fix3`, mark `-s blocked`, file a `-p 0 --parent "$EPIC" -l escalation -l friction` bead, and continue all non-dependent tasks in the epic.
- **Mandatory End-of-Epic Curator Gate (Run before `task_completed`):**
  Before closing the epic or signaling `task_completed`, **always** run:
  ```bash
  python3 ~/.config/opencode/skills/beads-orchestration/scripts/check-epic-friction.py "$EPIC"
  ```
  If it exits `0` (`CURATE_TRIGGERED: ...`) — or if any friction/rework/failure occurred during the epic:
  1. Create a curator bead, start `backlog-curator`, and wait for it to finish:
     ```bash
     CURATOR_BEAD=$(bd create "Curate epic $EPIC" -t task --parent "$EPIC" --silent)
     scion start "${CURATOR_BEAD}-curator-backlog-curator" --non-interactive --type backlog-curator \
       "Execute bead ${CURATOR_BEAD}: curate epic ${EPIC} and report to agent:${SCION_AGENT_NAME}"
     ```
  2. Schedule a 4m reminder (`scion schedule create --in 4m --agent "$SCION_AGENT_NAME" --message "Check backlog-curator"`), run `sciontool status blocked "Waiting for backlog-curator"`, and when it finishes, delete its container (`scion delete ... --non-interactive --preserve-branch`) and include its learnings in your final report.

## 3. Playbooks & Workspace Mode

- **Bugfix:** 1 `bug` bead on `bugfix/<ticket-id>-<short-summary>` → `developer` → delete (`--preserve-branch`) & `code-reviewer` → friction check.
- **Feature (ALWAYS a multi-agent team, NEVER a single developer):** `epic` + `feature/<ticket-id>-<short-summary>` branch → `architect`/`investigator` (if design/discovery needed) → split into multiple disjoint-path task beads with briefs on `/scion-volumes/scratchpad/` → parallel `developer` waves + `test-engineer` / `doc-writer` (with Markdown feature documentation in `documentation/` or `docs/`, or create `.docs/` if neither exists) → `code-reviewer` + `qa-tester` → friction check & `backlog-curator` if triggered.
- **Mandatory Feature Documentation:** Every feature implementation must produce Markdown documentation (`*.md`) explaining the feature in the repository's existing `documentation/` or `docs/` directory — or, if neither folder exists, create `.docs/` (e.g., `.docs/<feature>.md`) and commit it to the branch.
- **Investigation:** parallel read-only `investigator` tasks → findings written to `/scion-volumes/scratchpad/projects/<slug>/` → synthesis on the scratchpad and epic close reason.
- **Migration:** `epic` + 1 task bead per module/package → parallel `developer` wave (3-strike flow-around) → per-unit review → friction check.
- **Cross-Project:** Target only user-named or `CONNECTED_PROJECTS` / `$ASF_PEER_PROJECTS` repos (`cross-project-delegation`).
- **`SCION_WORKSPACE_MODE`:** `shared-plain` (1 author or disjoint paths, local), `worktree-per-agent` (parallel `--branch`, local merge), `clone-per-agent` (`git push origin <branch>` + PR, or git bundle on `/scion-volumes/scratchpad/` if token is missing).

## 4. Human Communication

Dispatchers report milestones, questions, and final results to humans via `scion message --non-interactive` to `conv:<conversation.id>` (replies) or `@<email>` / `@$SCION_CREATOR` (terminal output is not seen), paired with the `sciontool status` signal. Workers still hand off to `agent:<dispatcher>`.
