# Scion primer for ASF

The Scion concepts this project depends on, in short, with the upstream document to
read for details. Upstream docs are in the submodule: `scion/docs-site/src/content/docs/`
(paths below are relative to it). Upstream source wins over this summary.

## Building blocks

| Concept | What it is | Upstream doc |
|---|---|---|
| Agent | One container running a harness (here OpenCode) on a task, with its own home, workspace and identity | `concepts.md`, `local/agent-lifecycle.md` |
| Project | Namespace for agents and settings (`.scion/`), usually one per git repo; has a UUID (`SCION_PROJECT_ID`) | `concepts.md` |
| Hub | Control plane: database, API, web UI (`localhost:8080`), templates, skills, harness-configs, env and secrets | `concepts.md`, `workstation/workstation-server.md` |
| Runtime Broker | Starts and stops agent containers on this machine (`localhost:9800`) | `concepts.md` |
| Profile | Runtime + overrides in `settings.yaml` (`profiles.local`): env, `harness_overrides` (volumes, env per harness) | `reference/settings-precedence.md` |
| Harness-config | Adapts a CLI agent to Scion: image, command, provisioner, hooks, auth | `reference/harness-settings.md`, `scion/harnesses/authoring-guide.md` |
| Template | A role: instructions, system prompt, skills, model size, env | `local/templates.md`, `reference/agent-config.md` |
| Skill | Folder with `SKILL.md` (+ scripts), mounted into the agent; loaded on demand | `local/skills.md` |

Settings live in `~/.scion/settings.yaml` (global) and `<project>/.scion/settings.yaml`.
Precedence and every key: `reference/settings-precedence.md`, `reference/scion-config-reference.md`.

## How an agent is assembled

1. **Harness-config** base: image, command, model aliases, auth, `home/` files.
2. **Template** overlay (the `default` template is always the base of the chain):
   `agents.md` -> the harness's instructions file, `system-prompt.md` -> system prompt,
   `skills` -> skills dir, `env` -> fallback env, `home/` -> agent home.
3. **Platform additions**: a mandatory instruction preamble (workspace layout,
   `sciontool status` signals) and platform skills (`scion-messaging`,
   `scion-agent-manage`, ...; source in `scion/resources/platform_skills/`).
4. **Runtime overrides**: settings/profile env and volumes, Hub env and secrets, CLI flags.

Timing matters when you change things:

- `home/` files (harness-config and template) are copied only when an agent is
  **created** (or reincarnated). Restarting keeps the old ones.
- The harness provisioner bundle (`config.yaml`, `provision.py`, ...) is staged again
  on **every start**.
- Template `env` is read on every start. Templates and skills on the Hub are resolved
  when an agent is created.

## Templates: rules that bite

- Template `env` is a fallback; any other source wins. `SCION_*` keys are ignored,
  values are literal (no `${VAR}`), auth keys of the harness are never taken from it.
  We use `ASF_*` names for our switches.
- A template describes the job, not the environment: `image`, `volumes`, `resources`
  and `telemetry` in a template are ignored. Put them in the harness-config or settings.
- `model: small|medium|large|extra-large` is a size alias, mapped by the harness's
  `model_aliases`. Do not put concrete model names in templates.
- `message_mode: hub` lets an agent message across projects (our coordinator).
- ASF leaves harness selection out of templates. Use `--harness-config asf-opencode-<tools>`
  or a settings/project/Hub default; setup's default for new installations is
  `asf-opencode`. Upstream still supports `default_harness_config` in
  templates (`local/templates.md`, "Which harness-config is used"); omitting it is
  ASF's portability convention.

## Harness-configs

A harness-config is a folder: `config.yaml` (parsed by Scion), `provision.py`
(runs inside the container before the agent starts, stdlib Python, uses the vendored
`scion_harness.py`), optional `dialect.yaml` (maps the CLI's hook events to Scion
events), `agent-plugins.yaml`, and `home/`. Read `scion/harnesses/authoring-guide.md`
before changing one; it lists the provisioner contract (auth, instructions, MCP,
outputs), honest capabilities, and testing. Upstream harnesses (`scion/harnesses/*`)
are the best examples. Ours: [opencode-harness.md](opencode-harness.md), built on upstream `scion/harnesses/opencode`.

Install and publish: `scion harness-config install <dir>` then
`scion harness-config sync <name>` (our `scripts/build-harness.py` does both).

The Windows build of `scion` is a Hub client only: it cannot run the server, the
broker or agents (`hosted/user/windows.md`). On Windows, ASF runs inside WSL2.

## Inside an agent

- Status signals: `sciontool status ask_user|blocked|task_completed "<text>"`. An agent
  that has nothing to do sets `blocked` and ends its turn; Scion wakes it with a message.
- Messaging: `scion message <recipient> "<text>"` with `agent:<name>`, `user:<name>`,
  `@<agent>`, `@<email>`, `conv:<id>`; prefer `--body-file` for long text
  (`hosted/user/messaging.md`, platform skill `scion-messaging`).
- Scheduling a later check: `scion schedule create --in 20m ...` (`hosted/user/scheduling.md`).
- Workspace: `SCION_WORKSPACE_MODE` (shared, worktree or clone per agent);
  `/workspace` is the repo (`local/workspace.md`, `local/workspaces-and-sharing.md`).
- Shared dirs: project shared directories are mounted at `/scion-volumes/<name>`;
  ours is `scratchpad` (Beads workspace, build caches, trace ID).
- Secrets and env: Hub/project/user scopes (`hosted/user/secrets.md`).

## CLI you will use

```sh
scion list                                   # agents in the current project
scion start <name> --type <template> "<task>" [--harness-config asf-opencode]
scion attach <name> | scion logs <name> | scion stop <name> | scion delete <name>
scion message <name> "<text>"
scion templates list | scion template sync <name>
scion harness-config list | scion harness-config sync <name>
scion hub env get|set --scope hub KEY [VALUE] ; scion hub secret set --scope hub KEY VALUE
scion server start|stop|status               # Hub, hosted broker, web UI
```

Full reference: `reference/cli.md` (large; search it instead of reading it whole).
