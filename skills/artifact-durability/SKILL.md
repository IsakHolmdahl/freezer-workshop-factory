---
name: artifact-durability
description: >-
  Where a produced artifact has to end up so that it survives the agent that produced it,
  and what to do when no durable destination is available. Owns the property definition
  (reachable after your container is deleted), the list of durable destinations (git branch
  per SCION_WORKSPACE_MODE, Beads, shared scratchpad volume), and the fallback ladder.
  Use when you are about to write a deliverable to a path, when you are deciding whether
  your work is safe, or before you signal completion.
---

# Artifact Durability

## The property

> **An artifact is durable when it is reachable after your container has been deleted.**

Not written. Not saved. Not "in a local file." Reachable by someone else, later, once you
are gone.

You will not be consulted before your container is deleted, so every check you run on your
own work has to test that property and nothing weaker.

## Why this rule is stated as a property and not a place

This defect has been fixed at least six times in this repo, and each fix reintroduced it
one layer down, because each one tested something *easy to observe* instead of the thing
that *determines survival*:

| The check that was written | What it actually tests | What survives |
|---|---|---|
| "don't delete an agent with uncommitted work" | a commit exists locally | the commit is on the shared worktree branch or **pushed** to remote |
| "committing prevents losing work" | a commit exists locally | the commit outlives the container (`worktree-per-agent` branch or pushed ref) |
| "or written to the scratchpad" | a **path** was used | the path is **outside the container** (`/scion-volumes/scratchpad/`) or in Beads (`bd`) |

**A location satisfies a check. Only a property satisfies the purpose.** A path is evidence
of durability only when you know why that path outlives you — and one of the two paths this
repo has historically called "the scratchpad" (`/workspace/.scratch/`) does not.

## Durable destinations

Four things survive your deletion:

1. **A git commit on a surviving branch (per `$SCION_WORKSPACE_MODE`):**
   - **`worktree-per-agent` (default for linked repositories):** `/workspace` is a worktree
     of the host repository. The worktree directory is removed when your agent is deleted
     (with `--preserve-branch`), so uncommitted files vanish, while **commits on your work
     branch** survive in the shared host repository. Commit everything to your assigned work
     branch (`git status` clean) and include the branch name and commit SHA
     (`git log -1 --format=%H`) in your handoff. Push if a remote and credentials are
     configured; if `git push` fails due to missing/expired `GITHUB_TOKEN` or remote auth,
     do **not** fail the task — your local branch commit already survives in the host
     repository when the dispatcher deletes you with `--preserve-branch` (report the branch
     name, commit SHA, and note that remote push was skipped due to credentials).
   - **`shared-plain`:** `/workspace` is the shared directory itself and survives deletion;
     still commit to your branch and report branch and SHA.
   - **`clone-per-agent` (container-isolated clone):** `/workspace` is destroyed when your
     container is deleted. Commit and push your work branch (`git push -u origin <branch>`)
     before signaling completion. Push your work branch only — never the integration or `main`
     branch.      If `git push` fails in `clone-per-agent` mode due to missing `GITHUB_TOKEN`, export
     a git bundle (`git bundle create /scion-volumes/scratchpad/projects/<slug>/<branch>.bundle HEAD`)
     and report the path to your dispatcher so the dispatcher can apply it to a local branch.
2. **Storage mounted from outside the container** — the shared scratchpad volume at
   `/scion-volumes/scratchpad/` (e.g. `/scion-volumes/scratchpad/projects/<slug>/`), or a
   hosting bucket you have published to and verified. Durable because container deletion
   cannot reach it, not because of how the path is spelled. Use `/scion-volumes/scratchpad/`
   for all long-form non-code deliverables (briefs, design docs, research notes, QA/review
   reports, state files) and record the file path in your bead's `bd close -r "..."` reason.
3. **Beads (`bd`) on the shared Dolt server** — task status, close reasons (`bd close <id> -r "..."`),
   short notes/verdicts (`bd comments add <id> "..."`), and project rules (`bd remember`).
   Stored in Dolt and `/scion-volumes/scratchpad/beads`, surviving container deletion.
4. **The body of a message you have already sent.** Content inline in the message (or sent
   via `--body-file`), not a filepath pointing at storage only you can see.

Everything else dies with you: the uncommitted working tree, an unpushed commit in
`clone-per-agent` mode, `/tmp`, a gitignored file, and `/workspace/.scratch/`.

## When only container-local storage is available

`/workspace/.scratch/` is a real fallback for temporary local scratch, and an environment
with no shared volume is real. It is **not** a delivery destination, for two reasons — and
the second is the one that gets missed:

- it does not survive your container, and
- it is **not shared with any other agent**, so it cannot carry a handoff either.

If `/scion-volumes/scratchpad/` is not mounted in your container, do not write your deliverable to `/workspace/.scratch/` and signal completion. Instead, run `sciontool status blocked "Shared scratchpad /scion-volumes/scratchpad/ is not mounted"` and notify your dispatcher.

## A gitignored deliverable is invisible to "commit and push"

Measured: with `.scratch/` in `.gitignore`, a file written under it leaves `git status`
reporting a **clean tree** and `git add -A` staging **nothing**.

So a completion rule of the form *"commit and push anything still sitting in the working
tree"* cannot see such a deliverable, and does not fail — it reports success. If your
deliverable is a file rather than a commit, name that file and confirm its destination
directly (on `/scion-volumes/scratchpad/` or in Beads). Do not infer that it is safe from a
clean working tree.

## Hardening against one hazard is not durability

When you protect an artifact, **name the hazard you did not cover.** Adding a file to
`.gitignore` protects it from `git clean` — a real fix for a real incident — while leaving
it exposed to container deletion, and while actively preventing it from being committed.
Two hazards; that entry closes one and deepens the other.

Protecting something against the hazard you just met makes it *feel* protected, and that
feeling is what stops the search for the second hazard.

## Signal completion only after your work is durable

A completion signal is widely read as permission to delete your container, and you will not
be consulted before that happens — so the last moment your work can still be saved is before
that message goes out. Before you send it:

- Confirm your code changes are committed to your work branch (`git status` clean).
- In `worktree-per-agent` or `shared-plain` mode, a local commit on your work branch is already durable in the shared host repo (provided the dispatcher deletes your container with `--preserve-branch`). If a remote push is attempted and fails due to missing/expired `GITHUB_TOKEN`, do not block completion — report the local branch and commit SHA so the coordinator/manager can merge it into a local branch ready to be pushed by the user.
- In `clone-per-agent` mode, push to your remote work branch; if `git push` fails due to missing `GITHUB_TOKEN`, save a git bundle (`git bundle create /scion-volumes/scratchpad/projects/<slug>/<branch>.bundle HEAD`) and report its path so the coordinator can apply it to a branch ready to be pushed.
- If you produced no code changes there is nothing to commit — do not manufacture a commit.

Commit/push to your own work branch, never the integration branch — merging to shared ground
is the manager/coordinator's gate, and committing/pushing your branch does not cross it.

## When your deliverable is not a commit

Some roles produce deliverables that live outside any repo working tree — design docs,
research notes, QA reports, state files, briefs. Write these as normal Markdown files to the
shared scratchpad volume (`/scion-volumes/scratchpad/projects/<slug>/`) — never to a
gitignored workspace path like `/workspace/.scratch/` — and record the scratchpad file path
when closing your bead (`bd close "$BEADS_ISSUE_ID" -r "DONE: /scion-volumes/scratchpad/..."`).

Before you signal completion, confirm the deliverable file exists and is non-empty on
`/scion-volumes/scratchpad/` where your container's deletion cannot reach it. If the shared
volume is unavailable, see **When only container-local storage is available** above.

## Related

- Durability-push versus integration-push, and who may merge to shared ground:
  `templates/eng-manager/agents.md`.
- Who may delete an agent, and why `--preserve-branch` is mandatory: `beads-orchestration`
  and `scion-agent-manage` → **Agent Lifecycle**.
