---
name: agent-state-continuity
description: >-
  How to maintain persistent state across container restarts and agent deletion using
  a scratchpad state file on `/scion-volumes/scratchpad/` alongside lightweight Beads
  (`bd`) task status. Covers the agent-name namespace rule, scratchpad storage, and
  wake-up reconciliation. Use when your role requires persistent state across sessions
  (coordinators, engineering managers, or any orchestrator that tracks workstreams).
---

# Agent State Continuity

## 1. Scratchpad State File + Lightweight Beads (`bd`)

Orchestrators persist state across sessions using a **state file on the shared scratchpad volume** (`/scion-volumes/scratchpad/projects/<slug>/<agent-name>-state.md`) for session context and workstream notes, paired with **Beads (`bd`)** for the structured task DAG (`bd ready`, `bd list --parent <epic>`). Never keep state files inside the `/workspace` git working tree.

### Wake-up reconciliation
On every wake-up:
- Read your `<agent-name>-state.md` on `/scion-volumes/scratchpad/projects/<slug>/`.
- Run `bd list --parent <epic>` (or `bd ready --parent <epic>`) and `scion list --non-interactive`.
- Workers claim their bead on start (`status=in_progress`, `assignee=<worker-name>`) and close it on completion (`status=closed`, `close_reason="DONE: ..."`), so `bd list --status=in_progress` and `scion list --non-interactive` immediately show which workers are active, finished, or stalled without extra label bookkeeping.

### The scratchpad state file
Keep your state file on the project scratchpad on the shared volume
(`<scratchpad>/projects/<slug>/<agent-name>-state.md`, typically under
`/scion-volumes/scratchpad/`). This is the only file copy — do not keep a workspace-root
copy, because the state file should never exist inside a git working tree.

Substitute your own agent name into the path, so an agent named `acme-web-coord` writes to
`<scratchpad>/projects/<slug>/acme-web-coord-state.md`.

**The namespace is your agent name, not your role.** One fixed name per role is one name for
every instance of that role, so the second agent of any role on one project writes the same
file — last writer wins, no error and no edit event, and read-before-write does not help
because by then the corruption looks like the reader's own file. The agent name is the only
defence against this collision.

## 2. Suggested structure for `<agent-name>-state.md`

```markdown
# [Role] State

## Last Updated
[timestamp]

## Active Epic & Workstreams
- [epic-id / workstream]: [status, current agent, branch, scratchpad brief/report paths, blockers]

## Pending Tasks
- [bead-id / task description]: [priority, dependencies, assigned agent]

## Completed This Session
- [what was done, by whom, bead-id, commit hashes or branch/PR, report paths]

## Decisions Made
- [decision]: [rationale]

## Notes for Next Session
- [anything the next session needs to know]
```

Read this file (alongside `bd list` and `scion list --non-interactive`) at the start of
every session to restore context. Update it at significant milestones and before signaling
completion.

## 3. When no shared volume exists

If the scratchpad volume is unavailable, rely on Beads (`bd`) and follow
`artifact-durability` → **When only container-local storage is available**. Do not write the
state file to a path inside the git working tree as a fallback — state files should not be
committed to any repository.
