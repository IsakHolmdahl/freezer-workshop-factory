---
name: orch-evaluation
description: >-
  Validate, lint, stress-test, and benchmark Scion agent templates, skills,
  and multi-agent workflows. Use when reviewing generated templates, running
  quality gates before release, diagnosing agent stalls or coordination
  deadlocks, or scoring orchestration quality.
---

# Multi-Agent Orchestration & Template Evaluation Rubric

This skill provides a 5-dimension evaluation rubric, automated static verification checks, and behavioral failure simulation procedures for auditing Scion templates and agent workflows.

---

## 1. The 5-Dimension Evaluation Rubric (100-Point Scale)

A passing score is $\ge 90/100$ with **0 Critical** and **0 Major** defects.

| Dimension | Weight | Focus Areas | Automatic Disqualifiers (Critical) |
|---|---|---|---|
| **D1: Schema & Structural Compliance** | 25 pts | `schema_version: "1"`, no forbidden keys, kebab-case directories, no `home/`, valid skill frontmatter | Forbidden keys (`harness:`, `harness_config:`, `explicit_workspace:`, `config_dir:`), missing `scion-agent.yaml` or `agents.md`, present `home/` directory |
| **D2: Lifecycle & Signal Safety** | 20 pts | Explicit `sciontool status` (`ask_user`, `blocked`, `task_completed`), zero `sleep`/polling, external callback pairing | Use of `sleep` or polling loops, missing blocked signal before wait, missing task_completed at exit |
| **D3: Topology & Handoff Integrity** | 20 pts | 1:1 role matching, acyclic wait graph, briefs on `/scion-volumes/scratchpad/projects/<slug>/briefs/` | Circular wait deadlock, unquoted shell injection hazards in brief passing, missing handoff protocol |
| **D4: Workspace & Durability** | 20 pts | Start-of-task workspace checks, disjoint file ownership in `shared-plain`, durable deliverable destinations | Deliverables written solely to `/tmp` or `/workspace/.scratch/`, unpushed git branches in `clone-per-agent`, uncoordinated edits in shared workspaces |
| **D5: Prompt Clarity & Structure** | 15 pts | Separation of `system-prompt.md` and `agents.md`, no copy-pasted CLI manuals, clear checklists | Missing persona/operational split, ambiguous instructions |

---

## 2. Automated Static Lint Suite

Execute this static check script against target template and skill directories:

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "=== Running Scion Template & Skill Linter ==="
ERRORS=0

# Check 1: Forbidden keys in scion-agent.yaml
echo "[Check 1] Checking for forbidden YAML keys..."
if grep -rnE '^[[:space:]]*(harness|harness_config|explicit_workspace|config_dir):' .scion/templates/ 2>/dev/null; then
  echo "FAIL: Forbidden keys found in template YAMLs!"
  ERRORS=$((ERRORS + 1))
else
  echo "PASS: Zero forbidden keys found."
fi

# Check 2: Required files & prohibited home directory
echo "[Check 2] Checking required files and home/ directory..."
for dir in .scion/templates/*/; do
  [ -d "$dir" ] || continue
  template=$(basename "$dir")
  for req in scion-agent.yaml agents.md system-prompt.md; do
    if [ ! -f "$dir/$req" ]; then
      echo "FAIL: Template $template missing $req"
      ERRORS=$((ERRORS + 1))
    fi
  done
  if [ -d "$dir/home" ]; then
    echo "FAIL: Template $template contains forbidden home/ directory"
    ERRORS=$((ERRORS + 1))
  fi
done

# Check 3: Schema version and instructions wiring
echo "[Check 3] Validating scion-agent.yaml schema..."
for yaml in .scion/templates/*/scion-agent.yaml; do
  [ -f "$yaml" ] || continue
  grep -q '^schema_version: "1"' "$yaml" || { echo "FAIL: Invalid schema_version in $yaml"; ERRORS=$((ERRORS + 1)); }
  grep -q '^description:' "$yaml" || { echo "FAIL: Missing description in $yaml"; ERRORS=$((ERRORS + 1)); }
  grep -q '^agent_instructions: agents.md' "$yaml" || { echo "FAIL: Missing agent_instructions in $yaml"; ERRORS=$((ERRORS + 1)); }
done

# Check 4: Skill frontmatter validity
echo "[Check 4] Validating skill frontmatter..."
for skill in .scion/skills/*/SKILL.md; do
  [ -f "$skill" ] || continue
  sname=$(basename "$(dirname "$skill")")
  head -n 1 "$skill" | grep -q '^---$' || { echo "FAIL: Missing frontmatter start in $skill"; ERRORS=$((ERRORS + 1)); }
  grep -q "^name: $sname" "$skill" || { echo "FAIL: Skill name mismatch in $skill (expected $sname)"; ERRORS=$((ERRORS + 1)); }
  grep -q '^description:' "$skill" || { echo "FAIL: Missing description in $skill"; ERRORS=$((ERRORS + 1)); }
done

# Check 5: Lifecycle protocols in agents.md
echo "[Check 5] Validating lifecycle status calls..."
for md in .scion/templates/*/agents.md; do
  [ -f "$md" ] || continue
  grep -q 'sciontool status' "$md" || { echo "FAIL: No sciontool status reference in $md"; ERRORS=$((ERRORS + 1)); }
  if grep -iE '\bsleep [0-9]+' "$md" | grep -v 'Never' | grep -v 'Do NOT'; then
    echo "FAIL: Found sleep command usage in $md"
    ERRORS=$((ERRORS + 1))
  fi
done

if [ "$ERRORS" -eq 0 ]; then
  echo "=== All Static Checks PASSED ==="
else
  echo "=== Static Checks FAILED with $ERRORS error(s) ==="
  exit 1
fi
```

---

## 3. Behavioral Failure-Mode Simulations

Evaluate candidate systems across 6 core scenarios:
1. **Worker Provisioning Failure:** If a sub-agent fails to start or times out, does the orchestrator catch the error and handle or retry, rather than hanging indefinitely?
2. **Concurrent Edit Collision:** In `shared-plain`, do workers write to non-overlapping paths or coordinate exclusive locks?
3. **User Escalation Routing:** When an agent needs human input, does it execute `sciontool status ask_user "<question>"` and send the question via `scion message --non-interactive conv:<id>` / `@<email>` (terminal output is invisible to humans), rather than an interactive modal that locks orchestration?
4. **Container Deletion Durability:** If all worker containers are deleted (`scion delete`), do deliverables persist in `/scion-volumes/` or remote git branches?
5. **Handoff Delivery Failure:** If `scion message` fails to send, does the agent retry once, log an error, and mark `sciontool status blocked` instead of silently exiting?
6. **Rework Loop Convergence:** If evaluation rejects a work product, does the system dispatch a fresh worker with targeted feedback and avoid infinite rework cycles?

---

## 4. Evaluation Report Format (`eval-report.md`)

```markdown
# Orchestration Evaluation Report

- **Target:** <System / Template Name>
- **Date & Auditor:** <Date> / `orch-evaluator`
- **Overall Verdict:** PASS / FAIL (Score: X/100)

## Dimension Scorecard
| Dimension | Score | Max | Findings Summary |
|---|---|---|---|
| D1: Schema & Structural Compliance | 25 | 25 | Clean schema v1, zero forbidden keys |
| D2: Lifecycle & Signal Safety | 20 | 20 | Explicit transitions, zero sleep loops |
| D3: Topology & Handoff Integrity | 20 | 20 | Acyclic wait graph, briefs on `/scion-volumes/scratchpad/projects/<slug>/briefs/` |
| D4: Workspace & Durability | 20 | 20 | Disjoint files, deliverables on `/scion-volumes/scratchpad/` (reports/designs) and git branches (code) |
| D5: Prompt Clarity & Structure | 15 | 15 | Sharp prompt/instructions separation |
| **Total** | **100** | **100** | **Ready for production** |

## Defect Ledger
*(List any findings with Severity [CRITICAL/MAJOR/MINOR], File:Line, and Remediation)*
```
