# AI Software Factory (ASF): project overview

Read this first. It explains what this repository is, how the parts fit together and
where to change what. Details live in the linked documents; do not copy them elsewhere.

## What it is

ASF runs a team of AI agents that build software together. A **coordinator** agent
breaks a request into tasks, starts specialist agents (developer, architect,
reviewer, tester, ...) and drives the work to completion. It is built on:

| Part | Role | Where |
|---|---|---|
| [Scion](https://github.com/GoogleCloudPlatform/scion) | Runs agents in containers: Hub (state, API, web UI), Runtime Broker (starts containers), CLI `scion` | `scion/` (git submodule, upstream source, do not edit) |
| [OpenCode](https://opencode.ai) | The coding agent inside each container | `harness-configs/asf-opencode/` (our Scion harness, built on upstream `scion/harnesses/opencode`) |
| MLflow AI Gateway | The model router: serves one provider's models as endpoints `small`, `medium`, `large`; records traces | `mlflow/`, compose service `mlflow` |
| Beads (`bd`) + Dolt | Shared task tracker for agents: issues, dependencies, memories | `dolt/`, compose service `dolt` |
| Templates | Agent roles: instructions, system prompt, skills, model size | `templates/<role>/` |
| Skills | Reusable procedures agents load on demand | `skills/<name>/SKILL.md` |

Everything runs on one machine (Linux, macOS, or Windows inside WSL2; see
[setup-steps.md](setup-steps.md#platforms)). The Hub runs locally with its own
hosted Runtime Broker (`scion server start`). Agent containers use host networking,
so they reach MLflow at `localhost:5001`, Dolt at `127.0.0.1:3307` and the Hub at
`localhost:8080`.

## How a request flows

1. A human starts a coordinator (`scion start <name> --type coordinator "<task>"`) or
   talks to it in the web UI.
2. The coordinator creates an epic and task beads in Beads (`bd create`), then starts
   workers named `<bead-id>-<topic>-<role>` (`scion start ... --type developer`).
3. Each worker reads its bead (`BEADS_ISSUE_ID` is set from its name), does the work,
   comments the result on the bead, messages its dispatcher (`scion message`) and
   signals `sciontool status task_completed`.
4. The coordinator waits through Scion notifications (never by polling), verifies,
   and reports to the human. At the end of an epic it may start a `backlog-curator`
   when there was friction (`skills/beads-orchestration/scripts/check-epic-friction.py`).

Lessons that should outlive an agent go into Beads memories (`bd remember`); every
agent gets the project's memories in its instructions at start.

## Repository layout

| Path | Contents |
|---|---|
| `Makefile` | User commands: `make setup`, `make doctor`, `make cleanup`, `make test`; automatically prepares `.venv`, see [setup-steps.md](setup-steps.md) |
| `setup.py`, `scripts/setup.py` | Guided installer (do this first). Its steps are standalone modules in `scripts/steps/`, see [setup-steps.md](setup-steps.md). Steps run in order, one at a time (see setup-steps.md); `./setup.py --doctor` checks everything (`--fix` applies safe repairs) |
| `cleanup.py`, `scripts/cleanup.py` | Remove what setup created; data volumes only with `--volumes` or a ticked menu entry. See [setup-steps.md](setup-steps.md) |
| `setup.ps1` | Windows entry point: checks WSL2 and Docker, then runs `make <target>` inside WSL |
| `compose.yaml` | Core stack: MLflow (+ Postgres, RustFS S3), Dolt; includes `compose/telemetry.yaml`. Scion is not a Compose service |
| `harness-configs/asf-opencode/` | ASF OpenCode harness: see [opencode-harness.md](opencode-harness.md) |
| `templates/` | Agent roles: see [templates-and-skills.md](templates-and-skills.md) |
| `skills/` | Skills published to the Hub as `skill://scion/global/<name>` |
| `mlflow/bootstrap/` | MLflow startup: endpoint seeding for the configured provider (`seed_gateway.py`), trace sessions |
| `dolt/` | Dolt server image for Beads |
| `telemetry/` | OTEL collector, Prometheus, Grafana, and the run exporter that groups agent metrics per epic (see `TELEMETRY.md`) |
| `scripts/` | Build, sync and inspection tools (below) |
| `catalog/` | Multi-agent team archetypes used by the orch-* templates |
| `docs/` | This documentation |

Key scripts:

| Script | Does |
|---|---|
| `scripts/sync-factory.py` | Build base image, `scion` CLI and the `asf-opencode` images; publish skills and templates; set `DOLT_URL` |
| `scripts/build-harness.py` | Build an `asf-opencode` variant (`--tools pnpm,python`) and install its harness-config |
| `scripts/build-scion-base-image.py` | Build `scion-local-base` from the submodule |
| `scripts/doctor.py`, `scripts/verify-scion.py` | Health checks with fixes |
| `scripts/check-instruction-budgets.py` | Template contract and required coordination rules |
| `scripts/publish-skills-to-hub.sh`, `scripts/sync-process-skill.py` | Skill publishing (process skill is version-pinned) |
| `scripts/watch-beads.py` | Terminal view of beads, agents and friction |
| `scripts/scion-cross-project.sh` | Implements `scion cross-project` commands; saves peer selection as JSON in Scion home |

## Deployment

There is one: `scion server start` on this machine runs the Hub, its hosted Runtime
Broker and the web UI in one process (`scripts/deployments/local.py`). MLflow, Dolt
and telemetry run in Docker Compose next to it. `make setup-start` writes
`~/.scion/settings.yaml` and starts the server; `scion server stop|status` control it.

State lives in `~/.scion` (settings, Hub database, projects). The host needs the
`scion` CLI (built from `scion/` by `make all`). Setup recommends an existing
binary's location, preferring the first on `PATH`, and saves your choice as
`scion.install_path`; fresh installs default to `~/.local/bin/scion`.
The install places a POSIX `sh` ASF shim at that path and the unmodified upstream binary
alongside it as `scion-real`. `scion local …` and `scion cross-project …` run
installed shell extensions using `jq`, without Python or checkout dependencies.
`scion sessions` lists running agent terminal candidates; `scion watch` arranges
their attach sessions in tiled panes and tmux windows (four panes per window by default).
Other commands are forwarded with `exec`. See
[CLI extensions](setup-steps.md#scion-cli-extensions) for installation and usage.
`scion completion` selects Bash, Zsh or Fish using `$SHELL`; setup adds an
idempotent completion block to the corresponding shell startup file.

Set `SCION_HOME=/absolute/path/to/scion-state` in `.env` to relocate host Scion
settings, database and projects (an exported `SCION_HOME` takes precedence).
Setup, doctor and cleanup use this location. Setup creates a
`~/.scion` symlink to it because the pinned upstream CLI resolves its global
directory through `$HOME/.scion`. If that directory already holds data, stop
Scion and move it to the new location yourself before rerunning `make setup`;
setup never replaces an existing directory or a link pointing elsewhere.

### Scion version

The Scion submodule is pinned to **v0.3.0-preview.5** (upstream has no stable
release yet). `SCION_SOURCE_REF` in `.env` overrides the source tag, branch or
commit for setup, CLI builds and image builds; process environment takes precedence.
Setup preserves your `.env` choice. Existing `SCION_SOURCE_REF=main` values remain
explicit overrides: change them to `v0.3.0-preview.5` to use the pin. Then run
`make setup-scion` and rebuild images with `make setup-images`. Source selection
refuses to switch a dirty checkout and fetches missing refs from the Scion remote.

## Configuration

| File | Holds | Written by |
|---|---|---|
| `asf.yaml` | Common settings with comments: Scion URLs, agent gateway, MLflow port, model provider and endpoint models, Dolt, Beads prefix | committed; override per machine in `asf.local.yaml` (gitignored) |
| `.env` | Docker compose variables, generated from `asf.yaml` (other keys kept) | setup |
| `.env.secrets` | Model provider key (`MODEL_PROVIDER_API_KEY`), gateway credentials, Dolt passwords, admin identity; agent GitHub PATs are stored in the Hub and optionally the desktop keyring (see [setup-steps.md](setup-steps.md#optional-desktop-keyring-macos-linux-and-keepassxc)) | setup, from `.env.secrets.template` |
| `~/.scion/settings.yaml` | Scion settings: hub endpoint, identity, default harness (`scion.default_harness_config`, default `asf-opencode`), image registry, telemetry | `scripts/scion_settings.py` (keeps your edits except ASF-managed defaults) |
| Hub env/secrets | `AGENT_GATEWAY_URL/TYPE/API_KEY`, `DOLT_URL`, `DOLT_DB_PASSWORD`, `BEADS_PREFIX` (only when `beads.prefix` is set), tokens; `GITHUB_TOKEN` is a secret environment variable with `always` injection | setup (`scion hub env/secret set`) |

## CI and published images

GitHub Actions (`.github/workflows/`):

| Workflow | When | Does |
|---|---|---|
| `build.yml` | Every push and pull request; manual | Tests through `make venv`/`make test` on Python 3.10 and 3.12 (scripts, OpenCode harness, compose files), plus a real `python:3.12-alpine` missing-Python bootstrap/cache smoke test, then builds all images (`scripts/ci-images.py`). On `main`, `v*` tags and manual runs it pushes them, multi-arch, to `ghcr.io/<owner>/<image>` with tags `sha-<12>`, `latest` (main) and the version tag. Pull requests build amd64 locally and push nothing. |

Images and their bases: `python3 scripts/ci-images.py list`.

The registry images are pulled from is set in one place, `asf.yaml`
`scion.image_registry` (override in `asf.local.yaml`, e.g. `ghcr.io/<owner>` for the
CI-published images). `./setup.py` writes it to `.env` (`SCION_IMAGE_REGISTRY`, used
by the compose files; compose refuses to start without it) and to
`~/.scion/settings.yaml` (`image_registry`, used by Scion for agent images). CI
publishes to `ghcr.io/<owner>`.

## Working on this repository

- Change behaviour of agents in templates and skills, not in the harness.
  The harness only makes OpenCode run inside Scion (see [opencode-harness.md](opencode-harness.md)).
- Never edit `scion/` (upstream). To pick up upstream changes, update the submodule,
  copy `scion/harnesses/scion_harness.py` and the upstream OpenCode bridge files
  (`dialect.yaml`, `scion-bridge.js`, `opencode-launch.sh`) into `harness-configs/asf-opencode/`
  and run the tests (`provision_test.py` fails when they differ).
- Run the tests you touch:
  `.venv/bin/python -m pytest` (from the repo root; tests live in `tests/`),
  `cd harness-configs/asf-opencode && python3 -m unittest provision_test asf_entrypoint_test`.
- Changes reach agents only when published and when a **new** agent starts:
  templates/skills via `scripts/sync-factory.py --skip-build`, harness-configs via
  `scripts/build-harness.py --no-build`, images via a rebuild.
- Follow `AGENTS.md` (prefer libraries over custom code) and keep docs in `docs/`
  current when you change how things work.

## Further reading

- [scion-primer.md](scion-primer.md): Scion concepts we rely on, and where upstream documents them
- [opencode-harness.md](opencode-harness.md): how an agent container starts and is configured
- [templates-and-skills.md](templates-and-skills.md): roles, skills, budgets, publishing
- [agent-roles.md](agent-roles.md): waiting, routing and completion rules, and how to test them
- [cross-project.md](cross-project.md): coordinators delegating work to other projects, and how to limit it
- [slack-integration.md](slack-integration.md): talk to agents from Slack, or post Hub notifications to a channel
- [mcp-integration.md](mcp-integration.md): Scion MCP architecture, ASF use cases and future implementation reference
