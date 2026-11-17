# AI Software Factory (OpenCode edition)

A team of AI agents that build software together, on one machine:

- [Scion](https://github.com/GoogleCloudPlatform/scion) runs the agents in containers.
  The Hub, its hosted Runtime Broker and the web UI run locally (`scion server start`).
- [OpenCode](https://opencode.ai) is the coding agent in each container
  (`harness-configs/asf-opencode`, built on Scion's own OpenCode harness).
- The MLflow AI Gateway is the model router: agents ask for `small`, `medium` or
  `large`; MLflow forwards to the provider you configure and records traces.
- Beads (`bd`) on Dolt is the agents' shared task tracker.

Project overview and developer documentation: [docs/project.md](docs/project.md).

## Quick start

Linux and macOS:

```bash
git clone --recurse-submodules <this repo> && cd <repo>
make setup
```

Windows (PowerShell; runs everything inside WSL2, see
[platforms](docs/setup-steps.md#platforms)):

```powershell
.\setup.ps1
```

You need Docker (Compose v2 >= 2.24), git, make, Go >= 1.26, Node.js with npm,
and Python 3.10+. `make setup` creates `.venv` itself and tells you how to install
anything that is missing. On Windows these live in the WSL2 distribution, and the
checkout must be in the WSL filesystem.

Setup walks you through everything and can be run again at any time; finished
steps are skipped:

1. Prerequisites and configuration: `.env`, `.env.secrets`, your identity, and the
   **model provider** MLflow routes to, with its API key (the only credential setup
   needs; see [model provider](docs/setup-steps.md#model-provider)).
2. Services: MLflow, Dolt and telemetry in Docker Compose, then one small test
   call to each of the `small`/`medium`/`large` endpoints.
3. Scion: builds the CLI, Hub and web UI from the `scion/` submodule and installs
   the `scion` command.
4. Agent images: the Scion base image, `asf-opencode` and its tool variants
   (pnpm, Python).
5. Start: writes `~/.scion/settings.yaml` and starts the local Hub and broker.
6. Publish: optional GitHub PAT for the agents, roles (templates), skills, harness
   configs and Hub settings.
7. A final check (`make doctor`).

When it finishes:

| | |
|---|---|
| Scion web UI | http://localhost:8080 |
| MLflow (endpoints, traces) | http://localhost:5001 |
| Grafana (agent telemetry) | http://localhost:3000 |

## Start working

In a git repository you want the agents to work on:

```bash
scion start my-task --type coordinator "Add a health endpoint and tests"
scion list
scion attach my-task          # the agent's OpenCode session (detach: tmux prefix + d)
```

or start and talk to a coordinator in the web UI. Roles and how they cooperate:
[docs/agent-roles.md](docs/agent-roles.md).

## Everyday commands

```bash
make help                 # all commands
make doctor               # check the installation; every problem comes with a fix
make doctor-fix           # also apply the safe automatic repairs
make start                # start or update the services, Hub and broker
make setup-services       # after changing the provider, models or key
make setup-images         # rebuild the agent images
make setup-publish        # republish roles, skills and Hub settings
make cleanup              # remove what setup created (data volumes only when ticked)
scion server status|stop|start
```

## Configuration

| File | Holds |
|---|---|
| `asf.yaml` | Committed defaults: model provider and endpoint models, ports, default harness |
| `asf.local.yaml` | Your overrides, same structure (gitignored) |
| `.env.secrets` | `MODEL_PROVIDER_API_KEY`, Dolt passwords, your identity (gitignored) |
| `.env` | Generated from `asf.yaml` for Docker Compose |

Example `asf.local.yaml`:

```yaml
mlflow:
  provider: openai
  endpoints:
    small: gpt-5.5-mini
    medium: gpt-5.5
    large: gpt-5.5
```

Details: [docs/project.md](docs/project.md#configuration).

## Documentation

- [docs/project.md](docs/project.md): what the parts are and where to change what
- [docs/setup-steps.md](docs/setup-steps.md): platforms, model provider, setup steps, cleanup
- [docs/opencode-harness.md](docs/opencode-harness.md): how an agent starts and is configured
- [docs/scion-primer.md](docs/scion-primer.md): the Scion concepts this project relies on
- [docs/templates-and-skills.md](docs/templates-and-skills.md), [docs/agent-roles.md](docs/agent-roles.md): roles and skills
- [TELEMETRY.md](TELEMETRY.md): metrics and dashboards
