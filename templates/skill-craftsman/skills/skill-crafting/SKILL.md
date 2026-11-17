---
name: skill-crafting
description: >-
  Design, author, and refactor reusable agent skills following the
  agentskills.io standard (SKILL.md and references/). Use when creating new
  domain or workflow skills, extracting repeated instructions from agents.md
  into modular skills, or optimizing skill descriptions and token economy.
---

# Agent Skill Crafting Guide

This skill guides the design, structure, frontmatter engineering, and token optimization of agent skills adhering to the [agentskills.io](https://agentskills.io/) standard within Scion.

---

## 1. Directory Anatomy & Structure

Agent skills follow a modular, harness-portable directory layout:

```text
skills/<skill-name>/
├── SKILL.md                 # REQUIRED: Frontmatter + core operational manual
├── references/              # OPTIONAL: Deep schemas, examples, secondary guides
│   └── reference-guide.md
└── scripts/                 # OPTIONAL: Helper verification or utility scripts
    └── verify-rule.sh
```

### Invariants:
- Directory name must be lowercase kebab-case.
- `SKILL.md` must be at the root of the skill folder.
- Relative paths inside instructions must resolve relative to the skill directory or workspace root.

---

## 2. YAML Frontmatter Specification

Every `SKILL.md` must begin with a YAML frontmatter block enclosed by `---`:

```yaml
---
name: skill-name
description: >-
  High-signal description specifying WHAT the skill does and WHEN the agent
  must invoke it. Include specific triggers, file types, or lifecycle events.
---
```

### Rules:
- `name`: Must exactly match the directory name (kebab-case).
- `description`: 2–4 sentences describing scope and activation conditions. Harnesses use this description for semantic discovery and automated tool loading; vague descriptions cause missed loads or token bloat.

---

## 3. Progressive Disclosure & Structure

- **Progressive Disclosure:**
  - Place core invariants, decision tables, syntax patterns, and step-by-step checklists in `SKILL.md`.
  - Move secondary guides or extensive lookup tables to `references/<topic>.md` when helpful for organization.
  - Reference them explicitly: *"For detailed schema definitions, consult `references/schemas.md`"*.

---

## 4. Instruction Sharpness & Behavioral Guardrails

LLMs perform best with explicit boundaries, testable invariants, and contrasting examples:

### Good vs Bad Examples:
- **Bad:** *"Make sure you commit properly."*
- **Good:** *"Commit each logical slice to your assigned branch. Never commit directly to main or integration branches under `shared-plain` mode."*
- **Bad:** *"Don't wait around."*
- **Good:** *"Run `sciontool status blocked '<reason>'` and end the turn. Never run `sleep` or polling loops."*

---

## 5. Harness Portability

Never bake assumptions about specific LLM harnesses (such as `.config/opencode/`, `.claude/`, or `.gemini/` internal directories) into skill instructions. Reference standard workspace directories, Scion CLI commands, or environment variables (`SCION_WORKSPACE_MODE`).
