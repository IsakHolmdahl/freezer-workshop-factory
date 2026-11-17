---
name: template-authoring
description: >-
  Author and update Scion agent templates (.scion/templates/<name>/) including
  scion-agent.yaml, agents.md, and system-prompt.md. Use whenever creating or
  modifying an agent role template to enforce schema v1 rules, forbidden-key
  guardrails, sciontool status protocols, and artifact durability.
---

# Scion Template Authoring Guide

This skill defines the contract, schema rules, lifecycle protocols, and authoring guidelines for Scion agent templates located in `.scion/templates/<role>/`.

---

## 1. Directory Structure & File Invariants

Each template directory must adhere to:
```text
.scion/templates/<template-name>/
├── scion-agent.yaml         # REQUIRED: Schema v1 metadata & configuration
├── agents.md                # REQUIRED: Operational rules, workflow, lifecycle protocols
├── system-prompt.md         # RECOMMENDED: Persona, cognitive stance, output style
└── skills/                  # OPTIONAL: Locally bundled agent skills
    └── <skill-name>/
        └── SKILL.md
```

### Prohibitions
- **NO `home/` directory:** Custom templates automatically inherit user/harness settings from `default`. Creating `.scion/templates/<name>/home/` shadows platform defaults and is forbidden.
- **Strict kebab-case:** Template and skill folder names must use kebab-case (e.g. `orch-architect`, `template-engineer`).

---

## 2. `scion-agent.yaml` Schema v1 Contract

### Allowed Fields
```yaml
schema_version: "1"                          # REQUIRED: Must be the string "1"
description: "Concise role summary"          # REQUIRED: Non-empty string
agent_instructions: agents.md                # REQUIRED: Relative path to instructions file
system_prompt: system-prompt.md              # OPTIONAL/RECOMMENDED: Relative path to persona
max_turns: 60                                # OPTIONAL: Integer > 0
max_duration: "45m"                          # OPTIONAL: Duration string ("30m", "1h")
env:                                         # OPTIONAL: Key-value string map
  ROLE_TYPE: "worker"
services:                                    # OPTIONAL: Sidecar daemon definitions
  - name: test-service
    command: ["cmd", "--flag"]
    restart: always
skills:                                      # OPTIONAL: Registry skill URI list
  - uri: "skill://scion/global/artifact-durability"
    optional: true
```

### Forbidden Keys (Strict Hard Fail)
The following keys are deprecated or corrupt runtime state; they MUST NOT appear in `scion-agent.yaml`:
- `harness:` (breaks runtime start)
- `harness_config:` (corrupts harness configuration)
- `explicit_workspace:` (triggers interactive prompts that hang non-interactive agents)
- `config_dir:` (corrupts container harness initialization)

---

## 3. Persona vs Operational Split

### 3.1 `system-prompt.md` (Persona & Cognitive Stance)
Defines *who* the agent is:
- **Content:** Role identity, domain expertise, analytical perspective, behavioral tone, concise output discipline.
- **Avoid:** Step-by-step procedural checklists, platform CLI command tutorials, or file lists.

### 3.2 `agents.md` (Operational Workflow & Protocol)
Defines *what* the agent does:
- **Content:**
  1. Workspace mode inspection (`pwd`, `SCION_WORKSPACE_MODE`, `SCION_WORKSPACE_GIT`).
  2. Sequential execution workflow.
  3. Durability rules (code in git branch; briefs, reports, and handoffs on `/scion-volumes/scratchpad/projects/<slug>/`).
  4. `sciontool status` state transition protocols.
  5. Completion handoff via `scion message --body-file` and exit.
- **Avoid:** Copy-pasting long `scion --help` command manuals.

---

## 4. Mandatory Lifecycle & Status Protocols

Every generated `agents.md` must implement explicit status transitions:

```bash
# 1. When asking a question to a user or coordinator:
sciontool status ask_user "<question>"
# (Never use interactive harness ask_user tools.)
# Terminal output is not seen by humans: also send every human-facing message
# (questions, status, final reports, direct-task replies) with
# scion message --non-interactive conv:<conversation.id> (reply) or @<email> / @$SCION_CREATOR (new message).
# Every agents.md needs a "Human Communication" section stating this.

# 2. When blocked / waiting on worker agents, events, or scheduled callbacks:
sciontool status blocked "<reason>"
# Immediately end turn. NEVER run sleep or polling loops.

# 3. Completion handoff:
# Verify durable artifact first, then:
scion message --non-interactive agent:<dispatcher> --body-file <summary-path>
# Check exit status. If successful:
sciontool status task_completed "<task title>"
```

---

## 5. Pre-Handoff Verification Checklist

Run these commands to verify template compliance before delivery:

```bash
# Verify schema version and forbidden keys
for f in .scion/templates/*/scion-agent.yaml; do
  grep -q '^schema_version: "1"' "$f" || echo "FAIL: schema_version in $f"
  grep -E '^[[:space:]]*(harness|harness_config|explicit_workspace|config_dir):' "$f" && echo "FAIL: forbidden key in $f"
done

# Verify no home directory exists
find .scion/templates/ -maxdepth 2 -type d -name "home" | grep home && echo "FAIL: forbidden home dir found"
```
