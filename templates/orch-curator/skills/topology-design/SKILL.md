---
name: topology-design
description: >-
  Select and design multi-agent coordination topologies, role boundaries,
  communication graphs, and orchestration blueprints in Scion. Use when
  designing a new agent team, refactoring an existing team's workflow,
  choosing between Coordinator-Worker, Pipeline, Panel, or Beads/Gas-Town
  patterns, or writing a team blueprint.md.
---

# Topology Design for Multi-Agent Systems in Scion

This skill guides the selection, decomposition, and contract specification of multi-agent topologies within Scion. It provides decision frameworks, blueprint templates, role boundary rules, and anti-pattern mitigations.

---

## 1. Canonical Topology Selection Matrix

Evaluate project constraints across these four canonical coordination patterns:

| Dimension | Coordinator-Worker (Hub & Spoke) | Beads / Gas-Town Convoy (`bd` Ledger) | Staged Pipeline (DAG with Gates) | Panel / Round-Table (Deliberative) |
|---|---|---|---|---|
| **Ideal Team Size** | 2–6 agents | 3–12+ agents | 3–6 agents | 3–8 agents |
| **Coupling Level** | Medium (central coordinator directs tasks) | Low (decoupled via Dolt issue ledger) | High (stage $N+1$ depends on stage $N$) | None (independent parallel analysis) |
| **Execution Flow** | Dynamic dispatch, phase-gated fan-out | Dependency-driven ready queue | Sequential linear or branch DAG | Single fan-out, fan-in synthesis |
| **Coordination Medium** | Briefs on `/scion-volumes/scratchpad/projects/<slug>/briefs/` | `bd create/ready/update/close` + `/scion-volumes/scratchpad/` | Gate validation scripts + committed artifacts | Per-member reports on `/scion-volumes/scratchpad/projects/<slug>/` |
| **Failure Recovery** | Coordinator restarts or replaces worker | Fresh agent claimed per rework bead | Pipeline stage re-runs previous step | Synthesizer identifies dissenting outlier |
| **Token Cost** | Moderate (orchestrator tracks state) | Low per worker (ephemeral task focus) | Moderate (context passed forward) | High (parallel independent runs) |
| **Primary Use Cases** | Feature development, bug fixing, meta-teams | Large multi-agent systems, issue queues | Compilers, CI/CD, build-and-eval pipelines | Architecture reviews, security audits, panels |

### Selection Decision Tree
1. **Is tasks work tracked in Dolt `bd` issues with parallel dependency graphs?**
   - $\to$ Use **Beads / Gas-Town Convoy**.
2. **Is work strictly sequential where each role refines the prior deliverable (e.g. Spec $\to$ Dev $\to$ Eval)?**
   - $\to$ Use **Staged Pipeline**.
3. **Do multiple independent experts need to analyze the same artifact without anchoring bias?**
   - $\to$ Use **Panel / Round-Table**.
4. **Is there a primary planner delegating discrete tasks to 2–5 specialists?**
   - $\to$ Use **Coordinator-Worker**.

---

## 2. Role Decomposition & Boundary Principles

### 2.1 The Single-Orchestrator Invariant
Every multi-agent team MUST have exactly one entry-point orchestrator that coordinates workers, interfaces with the caller/user, and signals overall task completion. Never create peer-to-peer teams with ambiguous dispatch authority.

### 2.2 Boundary Slicing Rules
Split agents along three sharp boundaries:
1. **Write Scope Isolation:** Two agents must never write to the same files concurrently. Slices must be partitioned by file paths, directory subtrees, or durable volumes.
2. **Verification Gates (Builder vs Evaluator):** Never allow builders to grade their own work without an independent evaluator role.
3. **Context Specialization:** Isolate broad domain skills (e.g. architecture design vs kernel implementation vs security auditing) into separate roles to keep context windows lean.

### 2.3 Workspace Mode Adaptation
- **`shared-plain`:** Filesystem and staging index are shared. Workers MUST own strictly disjoint directories or run sequentially. Orchestrators must enforce file ownership before dispatch.
- **`worktree-per-agent`:** Separate checkouts with private index/files. Coordinate branch naming (`feat/<agent>-<slice>`) and avoid conflicting remote pushes.
- **`clone-per-agent`:** Private files and git state. Durability requires remote pushes or `/scion-volumes/` mounts.

---

## 3. Blueprint Specification (`blueprint.md` Schema)

When designing a team, the orchestrator produces a `blueprint.md` file adhering to this schema:

```markdown
# Team Blueprint: <Team Name>

## 1. Objective & Coordination Topology
- **Goal:** <Clear mission statement>
- **Selected Topology:** <Coordinator-Worker | Beads Convoy | Pipeline | Panel>
- **Rationale:** <Why this topology fits constraints>

## 2. Role Directory & Write Scope Matrix
| Role Name | Template Path | Primary Responsibility | Owned Write Paths | Local Skills |
|---|---|---|---|---|
| `<role-lead>` | `.scion/templates/<role-lead>/` | Coordination, dispatch | bead descriptions | `topology-design` |
| `<role-worker>` | `.scion/templates/<role-worker>/` | Implementation | `src/<component>/*` | `<skill-name>` |

## 3. Communication & Handoff Graph
- **Dispatch Pattern:** `Execute bead <task>: read /scion-volumes/scratchpad/projects/<slug>/briefs/<worker>.md`
- **Status Transitions:**
  - Lead: Dispatches $\to$ `sciontool status blocked`
  - Worker: Starts $\to$ Executes $\to$ Verifies artifact $\to$ `scion message agent:<lead>` $\to$ `sciontool status task_completed`
  - Lead: Wakes on message $\to$ Verifies durable artifact $\to$ Deletes worker container (`--preserve-branch`)

## 4. Durability & Verification Plan
- **Durable Deliverable Target:** `/scion-volumes/scratchpad/projects/<slug>/` (reports/designs) and git branch (code)
- **Pre-Release Gate:** Independent evaluation role / script execution
- **Acceptance Criteria:** <Numbered list of verifiable outcomes>
```

---

## 4. Anti-Patterns & Failure Modes

1. **Circular Wait Deadlock:** Role A waits for Role B while Role B waits for Role A. Always enforce a Directed Acyclic Graph (DAG) for blocking dependencies.
2. **Daisy-Chained Chatter:** Unstructured message passing between peer workers without orchestrator mediation or ledger tracking. Causes hallucinated handoffs and lost state.
3. **Inline Shell Brief Injection:** Multiline briefs in `scion start`/`scion message`. Write briefs to `/scion-volumes/scratchpad/projects/<slug>/briefs/<worker>.md` and pass the bead ID and filepath.
4. **Ungated Fan-Out:** Starting 5+ parallel workers before verifying a single vertical slice. Always validate 1 worker path first.
5. **Orphan Container Leakage:** Forgetting to clean up finished worker containers (`scion delete <name> --non-interactive --preserve-branch`) after verifying their outputs.
