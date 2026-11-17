# Templates and skills

How our agent roles (`templates/`) and procedures (`skills/`) are built, checked and
published. Scion's rules for both: [scion-primer.md](scion-primer.md). Behaviour
rules every role must follow (waiting, routing, completion): [agent-roles.md](agent-roles.md).
When writing or reviewing a template, also load the `template-authoring` skill;
for skills, `skill-crafting`.

## Template anatomy

```text
templates/<role>/
├── scion-agent.yaml   # description, model, env, secrets, skills
├── agents.md          # operational workflow: what to do, commands, scratchpad & Beads, handoff
└── system-prompt.md   # identity and cognitive stance
```

- The role name is the directory name. ASF templates leave harness selection to
  settings and the launch request; they contain no `harness`, `harness_config`, or
  `default_harness_config` keys.
- Setup defaults new installations to `default_harness_config: asf-opencode`
  in `~/.scion/settings.yaml`, so roles launched without a flag use the ASF harness.
  Existing settings keep the operator's choice. Select a tool variant explicitly
  with `--harness-config asf-opencode-pnpm` or another installed ASF variant.
  Project and Hub defaults can override the broker default; see
  [Scion's harness resolution order](https://googlecloudplatform.github.io/scion/local/templates/#which-harness-config-is-used).
- For an existing installation, set `default_harness_config: asf-opencode`
  in `~/.scion/settings.yaml`, then apply it to a database-backed Hub with
  `python3 scripts/sync-hub-settings.py --sections default_harness_config`.
- `model: small|medium|large` only (mapped to MLflow endpoints by the harness).
- `env:` is a fallback and must not use `SCION_*`; our switches are `ASF_*`
  (see [opencode-harness.md](opencode-harness.md)). Secrets are declared under `secrets:`.
- `skills:` use `skill://scion/global/<name>` for skills in this repo.
  Every role attaches `output-discipline`.
- Never add a `home/` directory to a template.

Roles (one line each in `scion-agent.yaml` `description`): coordinator, eng-manager,
architect, investigator, developer, web-builder, test-engineer, qa-tester,
code-reviewer, doc-writer, researcher, release-notes,
backlog-curator, and the orchestration-design roles orch-architect, orch-curator,
orch-evaluator, template-engineer, skill-craftsman (see `catalog/archetypes.md`).

## Conventions every role relies on

- Work is tracked in Beads (`bd`) as a lightweight task DAG (`bd create`, `bd dep add`, `bd ready`, `--claim`, `bd close -r`) and backed by the shared scratchpad volume (`/scion-volumes/scratchpad/`). Workers are named `<bead-id>-<topic>-<role>`; their scratchpad brief (`/scion-volumes/scratchpad/projects/<slug>/briefs/<worker>.md`) holds the full instructions, including `Dispatcher:` and `Completion recipient:`.
- Non-code deliverables (briefs, designs, research notes, QA/review reports, state files) are written as normal Markdown files to `/scion-volumes/scratchpad/projects/<slug>/` (never container-local `/workspace/.scratch/` or `/tmp/`), and the file path is recorded in `bd close "$BEADS_ISSUE_ID" -r "..."`.
- Feature implementations always start a multi-agent team (never a single developer) and include committed Markdown documentation in `documentation/`, `docs/`, or `.docs/`.
- Handoff: close the bead (`bd close "$BEADS_ISSUE_ID" -r "..."`), `scion message --non-interactive agent:<dispatcher> --body-file ...`, check the exit status, then `sciontool status task_completed`.
- Waiting: schedule a wake-up reminder (`scion schedule create --in 10m --agent "$SCION_AGENT_NAME" --message "..."`), run `sciontool status blocked "<reason>"`, and end the turn; never sleep or poll.
- Lessons: `bd remember "<fix>" --key <topic>`; agents receive `bd memories` (and auto-resolved `JAVA_HOME`) at start.
- Output: terse, per the `output-discipline` skill.
- Security work is restricted: every role's `system-prompt.md` ends with a "Security changes" rule that allows it only through a skill named `security-change-procedure`. The factory ships without that skill and without a security role, so agents stop and escalate (bead label `escalation`) until a factory engineer writes the skill and attaches it to the roles that need it.

## Skills

```text
skills/<name>/
├── SKILL.md       # front matter: name, description (when to use); then the procedure
└── scripts/       # optional helpers, run from the skill directory
```

The `description` decides when an agent loads the skill, so state the trigger.
Keep skills harness-neutral (no harness-specific paths). Scion also injects platform
skills (`scion-messaging`, ...) into every agent; do not duplicate them.

Notable skills: `beads-orchestration` (coordinators; `scripts/check-epic-friction.py`),
`cross-project-delegation` (coordinators; see [cross-project.md](cross-project.md)),
`beads-work-tracking` (workers), `beads-hygiene` (curator), `software-engineering-process`
(version-pinned, see [agent-roles.md](agent-roles.md)), `agent-state-continuity`,
`output-discipline`, `artifact-durability`, `structural-code-search` (code-touching roles; steers agents to the `ast-grep` CLI), `code-review`, `template-authoring`, `skill-crafting`.

## Check and publish

```sh
python3 -B scripts/check-instruction-budgets.py      # required handoff/waiting/routing rules
.venv/bin/python scripts/sync-factory.py --skip-build --skip-binary   # publish skills + templates to the Hub
```

`sync-factory.py` publishes all skills with a content-hash version (unchanged skills
are skipped) and imports/syncs every template. Agents created afterwards use the new
versions; running agents keep what they were created with.
Setup's `hub_wiring` step runs the same sync and includes the full command error
when it fails, including failures after skill publishing during template import.
During publication it displays a `Publish [completed/total]` counter with the
current harness config, skill or template. Existing skill versions count as
completed after verification. The live indicator also shows elapsed time while
the current item is being uploaded; verbose command output is retained for errors.

Before changing a role, check `docs/agent-roles.md` for its smoke tests.
