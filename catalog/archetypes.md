# Scion Multi-Agent Team Archetypes Catalog

This catalog documents production-grade, pre-validated multi-agent topology archetypes for Scion. Orchestration teams and engineers can instantiate these patterns directly or adapt them to domain-specific workflows.

---

## 1. Coordinator-Worker (Hub & Spoke)

### Overview
A centralized lead agent orchestrates 2–6 worker agents. The coordinator decomposes incoming requirements, writes task briefs to durable storage, dispatches workers, waits for completions, and synthesizes the final output.

### Topology Diagram
```text
           ┌──────────────────────┐
           │     Coordinator      │
           └──────────┬───────────┘
                      │ dispatches & collects
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
  ┌───────────┐ ┌───────────┐ ┌───────────┐
  │ Worker A  │ │ Worker B  │ │ Worker C  │
  └───────────┘ └───────────┘ └───────────┘
```

### Role Blueprint
- **Lead / Orchestrator (`<team>-coordinator`):**
  - *Responsibilities:* Intake requirements, validate workspace boundaries, generate task briefs in `/scion-volumes/scratchpad/projects/<slug>/briefs/<worker>.md`, dispatch workers via `scion start`, block on `sciontool status blocked`, inspect outputs, clean up containers.
  - *Skills:* `topology-design`, `artifact-durability`.
- **Specialist Workers (`<team>-worker-*`):**
  - *Responsibilities:* Read assigned brief, execute task in designated workspace subtrees, ensure durable output survival, message completion to lead, signal `sciontool status task_completed`.
  - *Skills:* Task-specific domain skills.

### Best Used For
Feature development, vertical slice implementations, refactoring tasks, bug triaging.

---

## 2. Beads / Gas-Town Convoy (Dolt-Backed Decentralized Queue)

### Overview
A decoupled, ledger-driven architecture where work items are tracked as issues in Beads (`bd`). The coordinator sets up an epic and child task beads with clear acceptance criteria. Ephemeral workers ("polecats") claim ready beads, execute them independently, record notes, and close them.

### Topology Diagram
```text
         ┌────────────────────────┐
         │ Dolt Issue Ledger (bd) │
         └───────────┬────────────┘
                     │ queries ready beads
        ┌────────────┼────────────┐
        ▼            ▼            ▼
  ┌───────────┐┌───────────┐┌───────────┐
  │ Worker 1  ││ Worker 2  ││ Worker 3  │
  │(claims bd)││(claims bd)││(claims bd)│
  └───────────┘└───────────┘└───────────┘
```

### Role Blueprint
- **Epic Coordinator (`convoy-lead`):**
  - *Responsibilities:* Runs `bd create -t epic`, breaks down into task beads with `--design` and `--acceptance`, queries `bd ready`, launches ephemeral workers to process beads in parallel.
  - *Skills:* `beads-work-tracking`, `topology-design`.
- **Ephemeral Workers (`convoy-polecat`):**
  - *Responsibilities:* Runs `bd claim <id>`, updates status, implements change, runs tests, closes bead with `bd close <id>`, signals completion and terminates.
  - *Skills:* `beads-work-tracking`, `artifact-durability`.

### Best Used For
Large, parallelizable work queues, multi-day engineering campaigns, systems with fine-grained dependency graphs.

---

## 3. Staged Pipeline (Sequential DAG with Quality Gates)

### Overview
A linear or staged workflow where distinct agents handle progressive transformations of an artifact. Each stage is gated by an independent verification check before triggering the next role.

### Topology Diagram
```text
┌──────────────┐     ┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│ Investigator │ ──► │  Architect  │ ──► │  Developer   │ ──► │   Reviewer   │
│  (Discovery) │     │ (Design/Spec│     │ (Code Slices)│     │  (Eval Gate) │
└──────────────┘     └─────────────┘     └──────────────┘     └──────────────┘
```

### Role Blueprint
- **Stage 1: Investigator (`pipeline-investigator`):** Gathers logs, source context, user requirements; writes discovery report to durable volume.
- **Stage 2: Architect (`pipeline-architect`):** Consumes discovery report, writes specification and architectural design document.
- **Stage 3: Developer (`pipeline-developer`):** Consumes specification, writes code and unit tests.
- **Stage 4: Reviewer / Evaluator (`pipeline-evaluator`):** Audits code against spec, runs test suites, signs off on release or sends back for rework.

### Best Used For
Safety-critical features, formal verification, CI/CD pipeline automation, complex multi-step migrations.

---

## 4. Panel / Round-Table (Adversarial & Multi-Perspective Council)

### Overview
Multiple independent specialist agents evaluate the same problem or artifact in parallel without seeing each other's work to prevent confirmation bias. A synthesizer role merges the findings into a balanced consensus or decision matrix.

### Topology Diagram
```text
                      ┌──────────────────────┐
                      │    Artifact/Draft    │
                      └──────────┬───────────┘
                                 │ fan-out
         ┌───────────────────────┼───────────────────────┐
         ▼                       ▼                       ▼
  ┌──────────────┐        ┌──────────────┐        ┌──────────────┐
  │   Security   │        │ Performance  │        │ Architecture │
  │  Specialist  │        │  Specialist  │        │  Specialist  │
  └──────┬───────┘        └──────┬───────┘        └──────┬───────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │ fan-in
                                 ▼
                      ┌──────────────────────┐
                      │ Synthesizer / Judge  │
                      └──────────────────────┘
```

### Role Blueprint
- **Specialist Reviewers (`panel-member-*`):**
  - *Responsibilities:* Read target artifact, evaluate strictly through assigned domain lens (e.g. security, latency, maintainability), write independent findings to separate files.
- **Lead Synthesizer (`panel-lead`):**
  - *Responsibilities:* Dispatches all panel members simultaneously, blocks until all deliver reports, analyzes discrepancies, reconciles trade-offs, and writes unified consensus document.

### Best Used For
Architecture Decision Records (ADRs), major security audits, high-impact design choices, code reviews.
