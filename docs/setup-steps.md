# Setup steps, platforms and cleanup

`make setup` installs the factory. `make cleanup` removes what it created. Both are
built from small **step** modules in `scripts/steps/`; each step can also run on its
own, which is the way to repeat or debug one part without the whole installer.

Read [project.md](project.md) first for what the parts are. This document covers how
the setup pieces fit together.

## What setup installs

| Part | Where it runs | Started by |
|---|---|---|
| MLflow AI Gateway (+ Postgres, RustFS), Dolt, telemetry | Docker Compose (`compose.yaml`) | `services` step |
| Scion Hub, hosted Runtime Broker and web UI | one process on this machine: `scion server start` | `scion_start` step |
| OpenCode agents | containers the broker starts from the `asf-opencode*` images | `scion start ...` |

There is one deployment: the Hub runs locally with its own hosted broker. Nothing
needs a remote Hub, a GitHub Copilot login or a cloud account; the only credential
setup asks for is the API key of the model provider MLflow routes to.

## Platforms

| Platform | How | Notes |
|---|---|---|
| Linux | `make setup` | Docker Engine with the Compose v2 plugin (>= 2.24) |
| macOS | `make setup` | Docker Desktop (enable host networking: Settings > Resources > Network) |
| Windows | `.\setup.ps1` in PowerShell, or `make setup` inside WSL2 | Scion's server and broker do not run on native Windows (`scion/docs-site/src/content/docs/hosted/user/windows.md`), so everything runs in a WSL2 distribution |

Windows details:

1. Install WSL2 and a distribution (`wsl --install -d Ubuntu`), and Docker Desktop
   with **Settings > Resources > WSL integration** enabled for that distribution
   (or Docker Engine inside the distribution).
2. Clone the repository **inside the WSL filesystem** (for example
   `~/ai-software-factory-opencode`), not under `/mnt/c`: file modes, symlinks and
   Docker bind mounts are unreliable on Windows drives. Setup refuses a checkout
   under `/mnt/<drive>`.
3. Run `.\setup.ps1` from that folder in PowerShell (open it through
   `\\wsl.localhost\<distro>\...`), or `make setup` in the WSL shell. `setup.ps1`
   checks WSL2, Docker and `make`/`git`, then runs the Make target inside WSL:
   `.\setup.ps1 doctor` runs `make doctor`.
4. Use the web UI from Windows at `http://localhost:8080` (WSL2 forwards localhost).
   Run `scion` commands in the WSL shell.

Rootless Docker for Testcontainers and the desktop keyring are Linux/macOS desktop
features; on WSL setup skips them unless you configured them yourself.

## Model provider

MLflow routes the gateway endpoints `small`, `medium` and `large` to one provider.
`asf.yaml` holds the defaults; override them in `asf.local.yaml`:

```yaml
mlflow:
  provider: openai              # anthropic (default), openai, gemini, openrouter, openai_compatible, ollama, github_copilot, ...
  api_base: ""                  # only for openai_compatible, ollama or a proxy
  endpoints:
    small: gpt-5.5-mini
    medium: gpt-5.5
    large: gpt-5.5
```

The key is `MODEL_PROVIDER_API_KEY` in `.env.secrets`. Interactive setup asks for
the provider and the key once (hidden input) when the key is missing; `--yes`
never asks and reports that the endpoints will not answer yet. `ollama` needs no
key; its `api_base` is the URL as seen from the MLflow container
(`http://host.docker.internal:11434`).

### GitHub Copilot

With a Copilot subscription, MLflow can route the endpoints to Copilot instead of
a paid API key:

```yaml
mlflow:
  provider: github_copilot
  endpoints:                    # Copilot model IDs use dots
    small: claude-haiku-5.5
    medium: claude-sonnet-5.5
    large: claude-sonnet-5.5
```

Picking Copilot in the setup prompt writes these. No `MODEL_PROVIDER_API_KEY` is
needed; MLflow keeps a GitHub OAuth login in the `copilot-token` volume instead.
The `copilot` step (also `make setup-copilot`) puts a valid one there in one of three ways:

- **browser**: GitHub device login. The step restarts MLflow, shows the code,
  opens https://github.com/login/device and detects the login.
- **token**: paste a Copilot-enabled GitHub OAuth token (`gho_...`, from a Copilot
  login, not a PAT) into a hidden prompt.
- **local**: reuse the Copilot login of an editor on this machine
  (`~/.config/github-copilot/apps.json` or `hosts.json`); offered only when found.

Each token is checked against Copilot before it is written to MLflow through stdin;
MLflow then restarts. To replace a stored login: `make setup-copilot COPILOT_ARGS=--replace`.
Unattended: `printf %s "$TOKEN" | .venv/bin/python scripts/steps/copilot.py --token-stdin`.
After switching the provider, recreate the container (below) before logging in.
LiteLLM's Copilot adapter would otherwise rewrite agents' system prompts; see
[mlflow/README.md](../mlflow/README.md#github-copilot).

To change provider, models or key later: edit `asf.local.yaml` / `.env.secrets`,
then run `make setup-services`. It rewrites `.env`, and the `endpoints` step
re-seeds the gateway (`mlflow/bootstrap/seed_gateway.py` updates the stored key
and the endpoint models) before it probes each endpoint. If the provider itself
changed, recreate the container so it gets the new environment:
`docker compose up -d --force-recreate mlflow`.

Agents never see the provider key. They get `AGENT_GATEWAY_URL`,
`AGENT_GATEWAY_TYPE` and `AGENT_GATEWAY_API_KEY` (a placeholder for the keyless
local gateway) from the Hub; see [opencode-harness.md](opencode-harness.md).

## Make commands and the Python environment

Run `make setup` from the repository root. Make prepares `.venv` using the existing
`setup.py` bootstrap, including its pip/ensurepip fallbacks; no activation is needed.
It reuses the environment and refreshes dependencies when `scripts/requirements.txt`
or `setup.py` changes. A missing environment is recreated automatically.

If neither the selected Python nor an existing environment is available, Make
asks whether to run the command in `python:3.12-alpine`. Answering No or providing
no input stops the command. Docker must be installed. The container installs its
CLI/build tools, mounts the checkout and your home at their original paths, and
uses the host Docker socket and host networking. Python dependencies are cached
in the `asf-python312-alpine` Docker volume, separately from the host `.venv`.
Files are written with your user ID (and the Docker socket group when present).
The Scion server would then start inside this temporary container and stop with
it: install Python 3.10+ on the host for a Hub that keeps running.

| Command | Purpose |
|---|---|
| `make setup` | Guided installation |
| `make start` | Start or update everything after setup: `compose up --build` for the services (applies changed images, services and settings even when all are healthy), then the Hub and broker |
| `make setup-auto` | Accept setup defaults |
| `make setup-dry-run` | Preview all setup steps |
| `make setup-services` | Configuration, core services and the model endpoint check |
| `make setup-scion` | Choose CLI location and build Scion |
| `make setup-cli` | Install/update the shim, shell extensions and completion config without building Scion |
| `make setup-images` | Build the OpenCode agent images |
| `make setup-start` | Write Scion settings and start the local Hub and broker |
| `make setup-github` | Configure agent PAT and publish Hub settings |
| `make setup-github-rotate` | Replace agent PAT and publish Hub settings |
| `make setup-publish` | Publish Hub settings, secrets, roles and skills |
| `make doctor` | Check the installation, including one small model call per endpoint |
| `make doctor-quick` | Check without model calls |
| `make doctor-fix` | Apply safe repairs and check |
| `make cleanup` | Interactive cleanup |
| `make cleanup-dry-run` | Preview cleanup |
| `make cleanup-auto` | Cleanup without prompts, retaining data volumes |
| `make cleanup-volumes` | Cleanup without prompts, including data volumes |
| `make venv` | Prepare Python dependencies only |
| `make test` | Install test dependencies and run Python tests |

`make help` lists the commands. `PYTHON=python3.12` selects the interpreter when
creating the environment. For other script options or selected pytest runs, use
the standalone commands below after `make venv`. Direct
`./setup.py` use still bootstraps the environment; `./setup.py --venv-only` prepares
it without running any installation steps.

## Running a step on its own

Scion source version selection is controlled by `.env` `SCION_SOURCE_REF`; see
[the project version pin](project.md#scion-version). Both full setup and standalone
build scripts select that ref before building.

Every module in `scripts/steps/` is a command with `--help`:

```bash
.venv/bin/python scripts/steps/services.py --help
.venv/bin/python scripts/steps/services.py --dry-run          # show what would change
```

Common flags, accepted by every step:

| Flag | Meaning |
|---|---|
| `--dry-run` | Show every command and write; run nothing that changes state. Read-only checks still run |
| `--yes` | Accept defaults, ask no questions (for unattended runs) |
| `--verbose` | Print every command |

Exit codes: `0` done or skipped, `1` a problem (the message is followed by `try:`
lines with the fix), `2` wrong usage, `130` interrupted with Ctrl-C.

Steps are **idempotent**. A second run of a finished step reports `↷ ... skipped`
and changes nothing. Steps can also be imported (`from steps import services`) and
called with `run(ctx, **options)`.

### Scion CLI extensions

Scion builds install a POSIX `sh` ASF shim at `scion.install_path` and the unmodified
upstream executable alongside it with a `-real` suffix (`scion-real` by default).
Rebuilds update both files. Forwarded commands use `exec`, preserving arguments,
interactive input/output, signals and exit status.

The shim installs POSIX `sh` extensions as `scion-local.sh`,
`scion-cross-project.sh`, and `scion-sessions.sh` alongside itself. All shim functionality runs without
Python or the ASF checkout. JSON parsing uses `jq`.

Setup checks for `jq`. If missing on Ubuntu or macOS, it offers to run
`sudo apt install -y jq` or `brew install jq` (Homebrew must be installed).
On other systems, it asks you to install `jq` manually and then rechecks PATH.
`--yes` reports the install command rather than installing system packages;
`--dry-run` previews the prompt.

Local-folder commands:

```bash
scion local --help
scion local init --broker hosted-broker
scion local start my-agent --type developer "Work on this folder"
scion local list
```

`scion cross-project setup` selects projects for coordinator communication and
applies the policy directly. `status`, `apply`, and `disable` are also available;
see [cross-project communication](cross-project.md). This command replaces the
cross-project task in the setup menu.

#### Agent sessions and multi-agent viewer

```sh
scion sessions                         # running agent session candidates, current project
scion sessions --all                    # across projects
scion watch                            # current project's running agents
scion watch agent-a agent-b             # select agents
scion watch --all --panes 4             # tiled panes, more tmux windows as needed
scion watch --project my-project --panes 2 --session my-view --detach
```

`watch` requires host `tmux` and `jq`. It creates a separate viewer session with
four panes per window by default (configurable from 1 to 8). Each pane runs
`scion attach` through the Hub, so remote brokers work too. The list shows running
agents as candidates; it does not inspect tmux inside each container. Managed
agents without terminals cannot be attached.

The viewer uses **Ctrl-a**: `Ctrl-a d` detaches, `Ctrl-a n` / `Ctrl-a p` changes
windows (tabs), and `Ctrl-a` followed by an arrow changes pane. Agent sessions
retain Ctrl-b. Detaching leaves the viewer and agents running. Within an existing
tmux client, `watch` switches to the new viewer instead of nesting a client.
`--detach` creates it without opening it; use `tmux attach -t my-view` later.
Session names are unique by default; an existing `--session` name is never replaced.

#### Local-folder behavior

Run these commands inside the local folder. The helper manages a non-Git Hub
project and a shared workspace; the folder must be mounted at the same path in
the combined Hub/broker. The installed shell implementation reads the existing
`~/.config/scion-local/projects/` state from the earlier helper.
Use `scion local --help` for its supported operations. Other commands, including
`scion --help`, retain upstream behavior.

To wrap an existing binary without rebuilding:

```bash
make setup-cli
# Or specify the binary and installation location explicitly:
python3 scripts/scion_install.py --binary /path/to/existing/scion \
  --install-path ~/.local/bin/scion
```

`make setup-cli` uses `scion.install_path` (or `INSTALL_PATH`), preferring the
existing upstream binary there, then `scion/build/scion`. It never builds Scion.

The source and install path may be the same; the installer saves the upstream
binary as `scion-real` before installing the shim. Repeating this command also
works when the install path already contains the shim. Cleanup removes the shim,
upstream binary and installed shell extensions.

#### Shell completion (Bash, Zsh, Fish)

`scion completion` detects the shell from `$SHELL` and emits the upstream Scion
completion script. An explicit shell (`scion completion bash`, `zsh`, or `fish`)
overrides detection, which is useful when your current shell differs from your
login shell. An unset or unsupported `$SHELL` requires an explicit shell.

Setup automatically adds a managed completion block to the detected shell's
startup file: `~/.bashrc`, `$ZDOTDIR/.zshrc` (default `~/.zshrc`), or Fish's
`config.fish` under `$__fish_config_dir` or `$XDG_CONFIG_HOME/fish` (default
`~/.config/fish`). Repeated installs update the same block, preserving surrounding
configuration. Zsh initializes completion if it is not already initialized.
The block checks that the installed CLI exists before loading completion.

Open a new shell after setup, or enable completion immediately in the current one:

```bash
# Bash (requires bash-completion).
source <(scion completion bash)

# Zsh.
autoload -Uz compinit && compinit
source <(scion completion zsh)
```

```fish
# Fish.
scion completion fish | source
```

Setup prints the updated config path after installing the shim. An unset or
unsupported `$SHELL` leaves startup files alone and prints manual instructions.
These scripts provide upstream Scion completions plus the shim's `local`,
`cross-project`, `sessions`, and `watch` commands, their subcommands and flags. Other completion
requests are forwarded to `scion-real`.

### The steps

In the order `make setup` runs them:

| Step (`scripts/steps/`) | What it does | Needs Scion | Examples |
|---|---|---|---|
| `prerequisites.py` | Checks the platform (native Windows and checkouts under `/mnt/<drive>` are refused with the fix), Docker, Compose >= 2.24, git, make, Go, npm and jq; initialises the Scion submodule if it is missing | no | `prerequisites.py --dry-run` |
| `configuration.py` | Writes `.env` (for Compose) and `.env.secrets`: model provider and its API key, admin identity, Dolt passwords. Saves a provider choice in `asf.local.yaml` | no | `configuration.py --yes` |
| `services.py` | Creates the external Docker network if missing, then starts MLflow, Dolt and telemetry with `compose.yaml` (`deployments/stack.start_core`); waits until healthy | no | `services.py --no-build` |
| `copilot.py` | Only for `mlflow.provider: github_copilot`: logs MLflow in to GitHub Copilot by browser device login, a pasted token or an editor's Copilot login; skipped with `--yes` ([details](#github-copilot)) | no | `copilot.py --replace`, `copilot.py --token-stdin` |
| `endpoints.py` | Seeds the MLflow gateway (provider key, endpoint models), then probes each agent gateway model with one small call; asks before continuing if one fails | no | `endpoints.py --yes` |
| `scion_cli.py` | Before the build, finds Scion on `PATH` and in `~/.local/bin`, `~/.config/scion/`, `/usr/local/bin` and the configured location. Asks where to install, saves `scion.install_path`, offers duplicate cleanup | no | `scion_cli.py --dry-run` |
| `build.py` | `scion`: Scion CLI, Hub and web UI from the submodule; installs the ASF CLI shim at the selected location and the upstream binary alongside it as `scion-real`. `images`: the base image, `asf-opencode` and its tool variants | no | `build.py scion --install-path ~/.local/bin/scion`; `build.py images --dry-run` |
| `testcontainers.py` | Experimental rootless Docker socket for Testcontainers tests in agents (Linux); unchecked by default in the setup menu | yes | `testcontainers.py --yes` |
| `scion_settings_step.py` | Writes `~/.scion/settings.yaml` atomically, 0600: Hub endpoint, identity, default harness, shared dirs, telemetry, Testcontainers socket | yes | `scion_settings_step.py --dry-run` |
| `scion_start.py` | Runs `scion server start` (Hub, hosted broker, web UI) and waits until the Hub answers. A running Hub is skipped | yes | `scion_start.py` |
| `secrets.py` | Guides dedicated fine-grained PAT creation, validates GitHub identity and repository access, publishes directly to the Hub; saves only `github.repositories` locally | yes | `secrets.py --dry-run`, `secrets.py --replace` |
| `hub_wiring.py` | Publishes Hub settings: admin role, shared env and secrets, gateway, git identity, harness configs, then the default harness from `scion.default_harness_config` ([details](opencode-harness.md#default-harness)) | yes | `hub_wiring.py --dry-run` |
| `doctor_step.py` | The final check. `--fix` applies safe repairs. Exit 1 if a check fails | yes | `doctor_step.py --fix`, `doctor_step.py --quick` |

"Needs Scion" means the step runs the `scion` CLI against the local Hub through
the adapter in `scripts/deployments/local.py`. The others read or write files and
run only host commands.

A step runs the same way whether `setup.py` calls it or you do.

## GitHub PAT for Scion agents

Setup's `secrets` step guides you through creating a dedicated
[fine-grained personal access token](https://github.com/settings/personal-access-tokens/new).
Choose the account or organization owning the repositories, an expiry and **Only
select repositories**. Limit the selection to repositories the agents need.

Repository permissions:

- **Contents: Read and write** for cloning and pushing code.
- **Pull requests: Read and write** for creating and updating PRs.
- **Metadata: Read-only** (included automatically).
- **Issues: Read and write** only if agents should manage GitHub issues.
- **Workflows: Read and write** only if agents should edit workflow files.

Get organization approval when required. For classic PATs, the fallback is `repo`
and optionally `workflow`; fine-grained PATs provide the recommended repository
restriction.

Enter the selected repositories as `OWNER/REPO` and paste the token into the hidden
prompt. Setup checks `/user`, repository metadata, Contents and Pull requests API
access, plus push access when GitHub reports it. It rejects failed checks before
saving. These read-only probes cannot prove all fine-grained write grants, branch
rules or workflow/issue permissions; confirm the permissions above in GitHub.
Empty repositories are accepted when GitHub confirms they have no branches.

The token is stored directly in the Hub, never in `.env.secrets`. You can also
keep it in your desktop keyring (below). Repository names go into `asf.local.yaml`
as `github.repositories`. The `secrets` step runs
after the Hub starts and stores `GITHUB_TOKEN` as a Hub-scoped environment variable using
`scion hub env set --scope hub --always --secret`, so it appears in the Hub's
environment resources with its value masked and is available during repository cloning
and to newly started/restarted agents. The scope shares the selected repository
access with agents on this Hub.

To configure or rotate the token after setup:

```bash
make setup-github-rotate
```

The token step first asks whether to configure access now. Answer **No** to
continue setup and configure it later with
`make setup-github`. You can also enter `s` at the
repository or token prompt to skip. Empty input at the hidden PAT prompt retries;
it never silently skips setup. An existing Hub token is detected through
metadata without retrieving its value. `--yes` skips collecting a new token.
Tokens saved in `.env.secrets` by older setup versions are validated, published
to the Hub and then removed from the file; an existing Hub token takes precedence.
Failed validation or publication preserves the legacy token for retry. Rotation
validates a replacement before updating the Hub. Scion's current CLI requires the environment value as a positional argument;
setup avoids printing its output on publication errors.

### Optional desktop keyring (macOS, Linux and KeePassXC)

The token guide offers to save a validated PAT in an available secure desktop
keyring. ASF uses Python's established [`keyring`](https://keyring.readthedocs.io/)
library: macOS Keychain, Linux Secret Service (including KeePassXC), KWallet or
Windows Credential Locker. It never falls back to plaintext credential files.
Headless installations can continue using Hub-only storage.

To explicitly enable keyring storage or reuse a saved token:

```bash
.venv/bin/python scripts/steps/secrets.py --keyring
```

If the Hub already has a token, setup leaves it in place. To save a replacement
in both the keyring and Hub, use `--keyring --replace` and paste the PAT. Future
`make setup-github-rotate` runs also update the configured keyring.
ASF stores the keyring preference as `github.credential_store: keyring` in
`asf.local.yaml`; the credential is stored under service `asf-agent-github`, with
the Hub URL (without a trailing slash) as the account. When that Hub has no token,
setup loads this credential, validates its repository access and republishes it.
The desktop keyring may ask you to unlock or authorize access, including under
`--yes`. A locked or failing configured keyring reports an error without using a
file fallback. Set `github.credential_store` to `hub` to return to Hub-only setup.

For **KeePassXC on Linux**:

1. Enable **Tools → Settings → Secret Service Integration → Enable KeePassXC
   Freedesktop.org Secret Service Integration** (requires a build with this feature).
2. In **Database → Database Settings → Secret Service Integration**, expose a
   dedicated group for ASF credentials and keep the database available/unlocked.
3. Ensure KeePassXC is the active Secret Service provider on your desktop session;
   only one provider can own it at a time (GNOME Keyring/KWallet may already do so).
4. Run the keyring command above from that desktop session. Entries are stored in
   the exposed KeePassXC group. KeePassXC's access-confirmation setting is supported.

On **macOS**, the native Keychain is used; KeePassXC's Linux D-Bus integration does
not provide a macOS backend. See the [KeePassXC Secret Service guide](https://keepassxc.org/docs/KeePassXC_UserGuide.html#_secret_service_integration).

## How the steps compose

```
setup.py                    menu + ordered steps
  └─ steps/<name>.py        run(ctx, **options) -> StepResult, one job each
       └─ Context.adapter   deployments/local.py: the local Hub and broker
            └─ deployments/ the only code that knows docker compose or scion server
```

- `steps/common.py` holds the shared contract: `Context` (adapter, config, dry-run,
  `--yes`), `StepResult`, `StepError` (message plus fix lines), and `cli()`, which
  builds a step's `main()`.
- `steps/ui.py` holds terminal helpers (headings, questions, guides). Prompts take
  their default under `--yes`.
- `cleanup.py` and `doctor.py` use the same `ComposeStack` and local adapter to list
  what exists and to check it.

`scripts/setup.py` calls each selected step's `run()` in registry order. Interactive
setup shows the task menu; the configuration step then asks for whatever is still
missing (model provider key, admin identity).
Menu, `--yes` and `--skip` runs include prerequisites, configuration and a final
doctor check. The GitHub-token guide runs with the
publish task, after Hub startup when start is also selected. With `--yes`, guides that
need typed input are skipped. `--only` runs exactly the named steps in the order
given; `--skip` runs everything except the named steps (prerequisites are kept).
Use `--list-steps` for the step names, for example `--skip build_images`.
Step errors stop setup with fix instructions.

Doctor checks the same Scion binary locations, even when Docker is unavailable.
It reports the recommended location, warns about duplicate installations and
configuration/PATH mismatches, and points to `./setup.py --only scion_cli` to choose
a location and optionally remove duplicates. Duplicate removal requires setup's
interactive confirmation; doctor does not remove binaries automatically.

Doctor includes a read-only broker-alignment check using
`scripts/realign-brokers.py`. When there is one online embedded broker, it checks
each non-global project's default broker, providers and linked checkout path.
`./setup.py --doctor --fix` re-plans and applies needed changes, retaining providers
that still have assigned agents. Ambiguous checkout paths require an explicit
`realign-brokers.py --project <slug> --path <slug>=/absolute/checkout --apply`.


## Compose files

| File | Contents |
|---|---|
| `compose.yaml` | core stack: MLflow, Postgres, RustFS storage, Dolt; includes the file below |
| `compose/telemetry.yaml` | OTEL collector, Prometheus, Grafana |

Scion is not a Compose service. The core stack has no `name:`, so Compose names the
project after the checkout directory: keep that name stable, or the volumes change.
`ComposeStack` (`scripts/deployments/stack.py`) is the one place that builds
`docker compose` commands; `scripts/deployments/local.py` is the one place that
starts, stops and calls Scion. Dry-run is enforced there: a mutating command is
logged, not run.

Includes in `compose.yaml` use `project_directory: .` (relative to `compose.yaml`).
CI runs `docker compose -f compose.yaml config -q` (`.github/workflows/build.yml`).

## Cleanup

`make cleanup` (or `scripts/cleanup.py`) lists what setup created, and removes what
you choose:

```bash
./cleanup.py --dry-run          # list only; nothing is removed
./cleanup.py                    # menu (needs .venv): tick the parts to remove
./cleanup.py --yes              # no questions; keeps the data volumes
./cleanup.py --yes --volumes    # no questions; also deletes the data volumes
./cleanup.py --skip config
```

Parts, in removal order:

| Key | Removes | Note |
|---|---|---|
| `server` | the local Scion server (`scion server stop`) | |
| `agents` | Scion agent containers | uncommitted work in them is lost |
| `stack` | the core Compose stack (`compose down`) | containers only; volumes are a separate part |
| `volumes` | data volumes of the core stack | **destructive**: Beads (Dolt), MLflow, Grafana |
| `network` | `ai-software-factory-network` | |
| `images` | images built by setup (Scion, agent, Dolt) | rebuilding takes a while |
| `scion-home` | `~/.scion` (Hub database, projects, login) | |
| `cli` | the installed `scion` binary and build output | |
| `config` | `.env` and `.env.secrets` | deletes the provider key and Dolt passwords |
| `venv` | `.venv` | unticked by default in the menu |

Volumes are removed only when you tick them in the menu or pass `--volumes`.
`--yes` alone never deletes data. Without Docker, the container parts are skipped
and cleanup says so.

Composable: `scripts/cleanup.py` exposes `plan()`, `default_selection()` and
`execute()` for other scripts. Each `Part` has a `key`, a `title`, the `items` it
would remove, a `warning`, and `destructive`.

## Tests

| File | Covers |
|---|---|
| `tests/test_cleanup.py` | the plan, volume selection, dry-run, `--yes`/`--volumes`/`--skip`, failure reporting |
| `tests/test_steps_*.py` | each step with a fake adapter: idempotence, dry-run, error fix lines |
| `tests/test_deployments_*.py` | the local adapter, `ComposeStack` argv, dry-run |
| `harness-configs/asf-opencode/*_test.py` | the OpenCode provisioner and the agent entrypoint |

The step and cleanup tests run with `timeout 60 pytest -x` and fail on any spawned
process, so they never touch Docker, Scion or the network. Run them with:

```bash
timeout 60 .venv/bin/python -m pytest -x tests/test_cleanup.py tests/test_steps_*.py
```
