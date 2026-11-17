---
name: software-engineering-process
description: >-
  How multi-agent engineering work is owned, sized, delegated, and verified from request
  to delivery. Covers the ownership tree, the independent-review invariant, sizing by
  delegation depth, the feature-team invariant, the review cycle, and briefing. Use when
  you are responsible for delivering an issue you will not implement yourself — classifying
  incoming work, deciding which roles to instantiate, creating and supervising agents, or
  governing a review cycle.
---

# Software Engineering Process

## Overview

How an engineering issue moves from request to delivered change when more than one agent is
involved: **who owns delivery, who creates whom, what each role owes, and when the work is
actually done.**

How to design, implement, and review is not here — read the role definitions and their own
skills for that.

## When to use

Use when you are **responsible for delivering an issue you will not implement yourself**:

- classifying an incoming issue and deciding how much process it warrants
- deciding which roles to instantiate, and creating those agents
- supervising agents you created and deciding whether to accept their output
- governing a review cycle
- deciding whether you are finished

**Do not use** as a developer, reviewer, architect, or investigator executing an assigned
task. Read your brief and your own role material instead.

---

## 1. Ownership

**Exactly one agent owns delivery of an issue at any moment.** That agent is the
**issue owner**. By default the issue owner is the coordinator. The issue owner:

1. creates the agents it needs,
2. briefs them,
3. monitors them,
4. accepts or rejects what comes back,
5. remains the issue owner until the issue is delivered.

### How an issue gets its owner

An issue is owned in one of two ways, depending on size:

- **Small issues / bugfixes (depth 1):** for a small isolated bugfix or one-line config tweak, the coordinator is the issue owner directly — it creates a developer and reviewer(s) and remains the issue owner itself.
- **Feature implementations & Chunky/Large issues (always a multi-agent team, never a single developer):** Any feature implementation **must** be executed by a multi-agent team — never delegated to a single developer. The coordinator (or dedicated issue owner / `eng-manager`) decomposes the feature across specialist roles (`investigator` / `architect` for scope and design, multiple parallel `developer` agents on disjoint task beads, `doc-writer` for documentation, and `code-reviewer` / `qa-tester` / `test-engineer` for verification).

In both cases, everything that follows about the issue owner applies equally: the
responsibilities are the same regardless of whether the coordinator fills the role itself
or delegates it to a dedicated agent.

**Only the top-level coordinator delegates issue ownership.** The issue-owner hierarchy
is at most two layers deep: coordinator, and optionally a dedicated issue owner beneath
it. A dedicated issue owner does not further delegate issue ownership.

Within an issue, the issue owner may delegate **implementation subtrees** — for example,
an engineering manager owns implementation and review for a set of developers. A
delegated agent creates, supervises, and accepts *inside* its subtree, but the issue
owner still accepts that subtree's deliverable.

> **A dedicated issue owner owns the full process for its issue** — this is precisely
> what delegation of issue ownership means. The coordinator delegates both authority
> and accountability for driving the SEP within that issue. What the coordinator
> retains is **project-level tracking**: it tracks all issues, monitors their dedicated
> owners, and remains responsible for the portfolio, not the per-issue process.

### Supervision follows creation

**The tree is the notification topology.**

| Mechanism | Consequence |
|---|---|
| Creating an agent **auto-subscribes** you to its notifications | You are the one told when it completes or stalls |
| Ancestry chains (`root → parent → child`) grant **transitive access** | Any ancestor can manage any descendant, at any depth |
| Subscriptions can also be project-scoped | Non-creators *can* observe, but only if wired deliberately |

**You are the one who will be told when it breaks. If you look away, nobody is looking.**

**The agent that creates workers is the one subscribed to their notifications.** If you
delegate oversight to an agent that did not create the workers, nobody is subscribed —
it looks wired but is silently disconnected.

### Definition of done for an issue owner

> **Your work is not complete while your subtree is live. Delivery is the deliverable, not
> dispatch.**

Dispatching agents and reporting that you dispatched them is not completion. Signal
completion when the work is delivered, not when it is handed out.

---

## 2. The invariant

Scale everything down with the size of the work — **except independent review.**

> **For any issue under work, the author and the reviewer are never the same agent.**

The constraint is scoped to the issue, not the tree. A coordinator may supervise several
issues at once and does not count toward it.

The reviewer's job is *not letting bad code through*. An agent that just argued itself into
a design cannot hold that goal against its own work. This is role conflict, not context
size — a bigger context window does not fix it.

---

## 3. Sizing

Sizing is **depth of the ownership tree** — not stage count, not agent count.

| Tier | Tree | Depth |
|---|---|---|
| Small (bugfix / single-concern tweak only) | coordinator → developer, reviewer | 1 |
| Feature / Chunky | coordinator → investigator, architect, eng-manager (or coordinator directly) → multiple parallel developers, doc-writer, code-reviewer, qa-tester | 2–3 |
| Large | coordinator → dedicated issue owner<br>dedicated issue owner → investigator, architect, engineering manager<br>engineering manager → multiple parallel developers, doc-writer, reviewers, testers | 3 |

The coordinator is the root at every size. Sizing is how many layers hang beneath it.

> **Hard Invariant for Feature Implementations:** A feature implementation is **never** a
> single-developer task. Even for a compact feature, the coordinator must start a team
> (e.g., `architect` or decomposed parallel `developer` beads across backend/frontend/tests/docs,
> plus `code-reviewer` and `qa-tester` / `test-engineer`) rather than handing the entire
> feature to one `developer`.

- **Small units:** Tasks target ~≤200 changed lines, one concern; developers make small
  atomic commits, reviewers reject oversized diffs as "split needed".
- **Mandatory Feature Team:** Never delegate an entire feature implementation to a single
  `developer`. Split features into multiple parallelizable beads across a team of agents
  (e.g., separate `developer` agents for distinct slices/components/tests, `architect` for
  design, `doc-writer` for documentation, `code-reviewer` and `qa-tester` for verification).

### Sizing rules

1. **Size up when uncertain.** Estimation is unreliable; over-sizing is the cheaper error.
2. **Every orchestrating role must collapse itself when handed less than its tier
   assumes.** An engineering manager given a trivial design relays it to a single
   developer instead of manufacturing phases.

**Never apply rule 1 without rule 2.** Over-sizing without collapse produces **ceremony
inflation**: an agent told it is an engineering manager performs engineering management,
because that is what the role description says the role does.

### Cost of supervision

A coordinator sustains roughly **2–3 issues concurrently**, and **tens of agents across a
session**. It scales because it holds very little detail per issue: it supervises rather
than does.

> **Do not carry per-issue detail you can re-read.** Design rationale, review findings and
> implementation state belong in Beads (`bd`) and scratchpad files (`/scion-volumes/scratchpad/`).
> Go read them when you need them.

Holding that state yourself is what takes you from three concurrent issues down to one.

---

## 4. The work

Roles under supervision, each owing an artifact to the agent that created it. Instantiate
only the roles the size calls for.

| Role | Receives | Owes |
|---|---|---|
| Investigator | the issue / bead | findings — facts, no design decisions |
| Architect | the investigation findings | an agreed design, iterated with a human when needed. Reads the findings; does not re-research |
| Engineering manager | the agreed design | implementation phases; the parallel-vs-sequential call; a supervised subtree of developers and reviewers |
| Developer | a brief / bead and a phase | a change on its work branch, ready for review |
| Reviewer | a change | a verdict, held to *do not let bad code through* |

The issue owner monitors each of these and accepts or rejects the artifact. An artifact
that comes back thin, off-brief, or stalled is the issue owner's problem to catch.

---

## 5. The review cycle

Run per **pull request or per developer** — not across the whole branch at once. A final
whole-branch review is sometimes warranted, but never as a replacement for the per-unit
cycle.

Each round: review → fixes → **new reviewer**. Repeat until a review comes back clean.

### Fresh reviewers and single-use workers

- **Reviewer is always replaced across rounds:** Sheds context. Avoids anchoring on its own
  prior review; attention decays across long reviews. A reviewer re-reading its own review
  commonly misses items it raised itself.
- **When a worker finishes its task:** Verify its durable artifact (for a developer:
  `git log -1 <branch>` matches the commit SHA in its handoff; for others: the report in the
  bead or on `/scion-volumes/scratchpad/`), then remove the worker container keeping the
  branch (`scion delete <worker> --non-interactive --preserve-branch`; plain `scion delete`
  removes the branch and its commits).
- **On `REQUEST CHANGES` or QA failure:** Create a `Fix:` bead (`-l rework`) and start a
  fresh developer on the work branch with the review findings, then a fresh `code-reviewer`
  for the re-review.

**Cap: 3–6 rounds** (3-strike cap `fix3` per task in `beads-orchestration`, up to 6 rounds
for multi-phase reviews), then escalate. Hitting the cap regularly means something upstream
is broken.

To escalate, hand your supervisor the round history, the unresolved findings,
what was already tried, and a recommended next action. **Do not open another round
without a decision.**

**Non-blocking is not optional.** It means "will not block the merge review." It does not
mean "skip it before shipping."

Every finding ends in **resolved** or **explicitly declined as not required**. Postponing an
accepted finding needs a human decision and a tracked follow-up bead — a reason written in
the review is not a disposition.

---

## 6. Context discipline

Separate agents per phase **are** separate context windows. **Do not collapse phases into
one agent to save time** — what you lose is the context reset, not just the division of
labour.

- A role boundary is a context reset.
- A fresh reviewer is a fresh window.
- Per-PR review scope keeps attention bounded.

Agent performance degrades well before the context window is full. Treat roughly **40%
utilisation** as the point where quality starts costing more than the saved handoff.

---

## 7. Briefing

**Every agent you create needs a brief, and no agent starts work without one.** An agent
you created that is doing the wrong thing is usually an agent you briefed badly.

Write the brief as a Markdown file on the shared scratchpad
(`/scion-volumes/scratchpad/projects/<slug>/briefs/<agent-name>.md`) using your `Write` tool
and pass the bead ID and scratchpad filepath in the `scion start` command. Keep the bead
itself lightweight (title, dependencies, status, close reason). For brief structure and
handoff, see `beads-orchestration` and `scion-agent-manage`.

---

## 8. Common rationalizations

| Rationalization | Reality |
|---|---|
| "I'm the engineering manager, so I should break this into phases." | A trivial design goes straight to one developer. Orchestration you did not need is waste you introduced. |
| "I'm the architect, so I owe a design document." | Handed a one-line fix, you owe a sentence and a decision. |
| "This change is small enough that I can review my own work." | You cannot hold both "make this work" and "do not let this through." |
| "I handed it off, so it's their problem now." | You delegated the work, not the delivery. |
| "My task was to dispatch the developers, and I dispatched them." | Delivery is the deliverable, not dispatch. You are not done while your subtree is live. |
| "I spawned the agents, so I'm done until they report." | An agent that never reports is exactly the case you exist to catch. Silence is not progress. |
| "The last reviewer approved it, so the follow-up fix is fine." | Fresh reviewer each round. Reviewers miss items on re-read of their own review. |
| "These findings are non-blocking, so I'll ship and fix later." | Non-blocking means it will not block merge review. It does not mean skip. |
| "I'll review the whole branch at once, it's more efficient." | Per-PR or per-developer. Whole-branch reviews dilute the attention the cycle depends on. |
| "I already know how to review code / plan work / design this." | You have generic priors, not this project's methodology. Read the role material. |
| "The brief is long, so I'll pass it inline." | Write it to the bead or the shared scratchpad (`/scion-volumes/scratchpad/`) and pass the bead ID or filepath — an inlined brief puts backticks and `$variables` into a shell command. See `scion-cli-operations` → **Shell Safety for Task Prompts**. |

## 9. Red flags

- You created an agent and have not heard from it, and have not gone to check.
- You are about to report completion while an agent you created is still running.
- The same agent wrote and reviewed a change.
- A reviewer is reviewing its own previous review's fixes.
- An architect is re-running investigation that was already delivered to it.
- You are relaying messages between two agents that could talk directly.
- Your tier says "engineering manager" and the design is three lines long.
- Review findings were recorded as non-blocking and never revisited.

## 10. Verification

Before reporting delivery:

- [ ] Every agent you created has reported, been accepted, or been explicitly stopped/deleted (`scion delete --preserve-branch`).
- [ ] Every change was reviewed by an agent that did not write it.
- [ ] Every implemented feature includes Markdown documentation (`*.md`) committed in the repository's `documentation/` or `docs/` directory (or in a newly created `.docs/` directory if neither existed).
- [ ] The final review round came back clean — not "clean except".
- [ ] Every non-blocking finding was resolved or explicitly declined. Nothing accepted is
      still pending.
- [ ] No subtree is still live.

**Run this immediately before you report — not as you go.** A box ticked earlier states
something about a moment that has passed: agents you created have since finished, stalled,
or spawned others. **Re-check; do not recall.**

## 11. Human Communication

Humans never see the terminal. Any message meant for a human (questions, status,
final reports, direct-task replies) is sent with `scion message --non-interactive`:
to `conv:<conversation.id>` when replying to a received message, otherwise to the
human contact in the brief or `@$SCION_CREATOR` (when it is an e-mail), alongside
`sciontool status ask_user/blocked/task_completed`. Agent handoffs use `agent:<dispatcher>`.

## 12. Related material

| For | See |
|---|---|
| EM brief template for issues with a dedicated issue owner | `references/em-brief-template.md` in this skill |
| Beads epics, waves, worker naming, friction & curation | `beads-orchestration` |
| Shell metacharacters and absolute paths in task prompts | `scion-cli-operations` → **Shell Safety for Task Prompts** |
| Briefing structure, model override, agent recovery, stall handling | `scion-agent-manage` |
| How to do architecture, design-artifact shape | architect role definition |
| Work slicing, parallel-vs-sequential technique | engineering-manager role definition |
| Review methodology and severity | `code-review` |
