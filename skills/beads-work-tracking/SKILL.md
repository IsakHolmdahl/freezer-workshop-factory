---
name: beads-work-tracking
description: >-
  Lightweight worker protocol for tracking assigned work in beads (`bd`) and
  `/scion-volumes/scratchpad/`: claim your bead in one command, read your scratchpad
  brief and repo docs, record resolved tooling blockers with `bd remember`, write reports to
  `/scion-volumes/scratchpad/`, and close with a 1-line summary.
---

# Beads Work Tracking (Lightweight Worker Protocol)

**Keep Beads lightweight:** `bd` tracks your task status (`--claim` → `close`), out-of-scope discoveries (`discovered-from`), friction labels (`-l friction`), and resolved tooling/build blockers (`bd memories` / `bd remember`).
All long-form documents (your task brief, design docs, research notes, QA/review reports, and handoff summaries) live as normal Markdown files on `/scion-volumes/scratchpad/projects/<slug>/` (and permanent feature docs in `documentation/`, `docs/`, or `.docs/` in the repo). Never pipe multi-page Markdown into `bd` flags.

`BEADS_DIR`, `BEADS_ACTOR` (`$SCION_AGENT_NAME`), `BEADS_DOLT_*`, `BEADS_ISSUE_ID` (from `<bead-id>-<topic>-<role>` names), `TRACE_ID`, `JAVA_HOME`, and shared build caches (`$ASF_BUILD_CACHE`, `/scion-volumes/build-cache`) are configured automatically at container start.

**Never run `bd init`, `bd doctor --fix` or `bd dolt start`:** the project database on the shared Dolt server is created and connected for you at start. If `bd` reports no database or a connection error, do not repair it: run `sciontool status blocked "Beads unavailable: <error>"` and tell your dispatcher/human contact.

## 1. Startup (Execute Immediately)

Do not narrate your plan and wait for confirmation. First action in every session:

```bash
bd update "${BEADS_ISSUE_ID:-<id>}" --claim   # sets status=in_progress and assignee=$SCION_AGENT_NAME
bd show "${BEADS_ISSUE_ID:-<id>}"             # check task title, notes, or linked scratchpad path
```

Then read your brief file on `/scion-volumes/scratchpad/projects/<slug>/briefs/...` (passed in your task prompt) using the `Read` tool.

## 2. How to Find Project Information

- **Build/test/environment rules:** Check the `bd memories` block already injected at the top of your `AGENTS.md` (or run `bd memories`), plus the repository's `AGENTS.md` / `CLAUDE.md`.
- **Feature & architecture context:** Read the design/research files on `/scion-volumes/scratchpad/projects/<slug>/` and the committed documentation in the repository's `documentation/`, `docs/`, or `.docs/` folders.
- **Do not mine old closed beads for general codebase knowledge:** Only use `bd show <id>` or `bd search "<topic>" --status all` when your brief specifically asks you to inspect a prior task or regression.

## 3. Work, Blocker Warnings (`bd remember`), Friction & Handoff

- **Stay in scope:** Edit only the files and branch named in your brief.
- **Record ONLY resolved tooling/build blockers (`bd remember`):** `bd memories` is injected into every agent's startup context, so keep it tiny. Use `bd remember` **only** when you hit a real build, test, or environment blocker that failed and found the fix, so the next agent does not try the same broken thing:
  ```bash
  bd remember "<1-line: do X instead of Y because Z>" --key <blocker-topic>
  ```
  Never put codebase facts, architecture notes, or general conventions in `bd remember` (those belong in `/scion-volumes/scratchpad/` or repo `docs/`). Always reuse `<blocker-topic>` to overwrite instead of creating duplicates.
- **Out-of-scope discoveries (no side-questing):** If you spot an unrelated bug or needed follow-up outside your owned files, do not fix it yourself — file it in 1 command and keep moving:
  ```bash
  bd create "<title>" --deps "discovered-from:${BEADS_ISSUE_ID}" -d "<1-line why and file:line>" --silent
  ```
- **Blocked:**
  ```bash
  bd update "${BEADS_ISSUE_ID}" -s blocked -l friction --append-notes "FRICTION: <reason>"
  ```
  Message `agent:<dispatcher>`, then run `sciontool status blocked "<reason>"` and end the turn. (If waiting on an external non-agent process, first schedule a wake-up reminder: `scion schedule create --in 4m --agent "$SCION_AGENT_NAME" --message "Reminder: check external process"`.)
- **Human interjection:** If a human user steps in directly to fix, unblock, or correct your work, immediately tag your bead:
  ```bash
  bd update "${BEADS_ISSUE_ID}" -l user-interjection -l friction --append-notes "USER_INTERJECTION: <what the user fixed>"
  ```
- **Close & Handoff (3 Steps):**
  1. **Save your deliverable:**
     - Code roles: commit to your `work/<bead-id>-*` branch (`git status` clean) including Markdown feature docs in `documentation/`, `docs/`, or `.docs/`. Never change the git commit author (`user.name` / `user.email` / `--author`). Format every commit message as **`<JIRA-TICKET-ID> <conventional commit message>`** (when a Jira ticket ID is in the prompt/brief, or `<conventional commit message>` otherwise) with the trailer **`Co-authored-by: factory-agent (ai-software-factory) <scion-agents@example.com>`**.
     - Report/design roles: write your Markdown report to `/scion-volumes/scratchpad/projects/<slug>/<bead-id>-<role>.md`.
  2. **Record in Beads (1 command):**
     - **Clean run:**
       ```bash
       bd close "${BEADS_ISSUE_ID}" -r "DONE: <branch@SHA or scratchpad report path, checks run>"
       ```
     - **If any build/test/tool snag delayed you:**
       ```bash
       bd update "${BEADS_ISSUE_ID}" --add-label friction
       bd close "${BEADS_ISSUE_ID}" -r "DONE: <branch@SHA or path>. FRICTION: <1-line cause and workaround>"
       ```
     - *(Reviewers/QA testing a developer's bead: if leaving the bead open or recording a verdict on a shared bead, use `bd comments add "${BEADS_ISSUE_ID}" "VERDICT: <APPROVE|REQUEST CHANGES|PASS|FAIL> — report at /scion-volumes/scratchpad/..."` and close your own review bead if separate).*
  3. **Message dispatcher & complete:**
     Write a short handoff summary file (e.g. `/scion-volumes/scratchpad/projects/<slug>/${BEADS_ISSUE_ID}-handoff.md`) and send it:
     ```bash
     scion message --non-interactive agent:<dispatcher> --body-file "/scion-volumes/scratchpad/projects/<slug>/${BEADS_ISSUE_ID}-handoff.md"
     sciontool status task_completed "${BEADS_ISSUE_ID}"
     ```
