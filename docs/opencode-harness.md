# How an OpenCode agent starts and is configured

What happens between `scion start` and a working OpenCode agent, which file controls
each step, and where to look when something is wrong. File-level reference:
`harness-configs/asf-opencode/README.md`. Scion background: [scion-primer.md](scion-primer.md).

ASF's harness config is `asf-opencode`, with tool variants named
`asf-opencode-<tools>`. Images use the same names. It is built on Scion's own
OpenCode harness (`scion/harnesses/opencode`): the hook bridge, hook dialect and
launch wrapper are upstream's files, unchanged; the provisioner, image and
entrypoint are ours.

## Models: MLflow as the router

OpenCode gets exactly one provider, `gateway`, an OpenAI-compatible endpoint
(`@ai-sdk/openai-compatible`) at `AGENT_GATEWAY_URL`: by default the local MLflow
AI Gateway, `http://localhost:5001/gateway/mlflow/v1`. Its models are the gateway
endpoints `small`, `medium`, `large` (plus any other endpoint MLflow serves).
Templates say `model: small|medium|large`; `model_aliases` in `config.yaml` maps
that to `gateway/<size>`. OpenCode's own providers (`opencode`, `github-copilot`)
are disabled, so an agent cannot fall back to anything else.

Which real model answers is decided in MLflow, not in the agent: `mlflow.provider`
and `mlflow.endpoints` in `asf.yaml` / `asf.local.yaml`, with the provider key
`MODEL_PROVIDER_API_KEY` in `.env.secrets`
([setup-steps.md](setup-steps.md#model-provider)). Agents never see that key.
Every request carries `X-MLflow-Session-Id` (the orchestration's `TRACE_ID`) and
`X-MLflow-Trace-User` (the agent name), so MLflow groups one orchestration into
one trace session.

## Default harness

`asf.yaml` `scion.default_harness_config` selects the default for new agents
(`asf-opencode`). Override it in `asf.local.yaml` to use another
installed variant, such as `asf-opencode-python`. Setup refreshes
the host settings default. Publishing installs the harnesses first, then updates the
running Hub's default through its settings API; this covers both CLI and web UI
starts. Explicit project, template or `--harness-config` selections retain their
upstream precedence. Existing agents retain the harness they were created with.

To apply the default and publish harnesses on an existing installation:

```sh
./setup.py --only scion_settings,hub_wiring
```

The chosen harness must be installed and its image available to the broker.

## Start sequence

| # | Step | Runs where | Controlled by |
|---|---|---|---|
| 1 | Hub resolves template chain (`default` + role), skills, model alias, env, secrets | Hub/broker | `templates/<role>/`, `skills/`, Hub env/secrets, `~/.scion/settings.yaml` |
| 2 | Broker creates the agent home (first start only): harness `home/` then template `home/` | host | `harness-configs/asf-opencode/home/` (plugins, launch wrapper, base `opencode.json`) |
| 3 | Broker starts the container from the harness image with mounts: workspace, shared dirs (`/scion-volumes/scratchpad`, `/scion-volumes/build-cache`) | host | `config.yaml` `image`, settings `shared_dirs`, `profiles.local.harness_overrides` |
| 4 | `sciontool init` runs pre-start hooks, including `provision.py` | container | `provision.py` + vendored `scion_harness.py` |
| 5 | `provision.py` merges into `~/.config/opencode/opencode.json`: the `gateway` provider (URL, `{env:AGENT_GATEWAY_API_KEY}`, MLflow trace headers, models with a context window), `model`, `small_model`, disabled providers, allow-all permissions, MCP servers; writes `AGENTS.md` (template instructions + Scion preamble) and the template system prompt as the primary agent's prompt; env outputs (`AGENT_GATEWAY_*`) | container | `harness-configs/asf-opencode/provision.py` |
| 6 | `asf-entrypoint`: Beads database/env, `TRACE_ID`, build caches, start-context block in `AGENTS.md` ("nobody reads your terminal", tool notes, `bd memories`) | container | `harness-configs/asf-opencode/asf-entrypoint.py`, `/etc/scion-tools/*.md` |
| 7 | `opencode --prompt "<task>"` through `opencode-launch.sh` (hides `GITHUB_TOKEN` from OpenCode, keeps git working with it) | container | `config.yaml` `command` |
| 8 | `scion-bridge.js` reports lifecycle, tool and model usage to Scion (`sciontool hook --dialect=opencode`); `asf-reminders.js` cancels the agent's pending `scion schedule` reminders when it wakes up or reports `task_completed` | container | `home/.config/opencode/plugins/`, `dialect.yaml` |

Restarting an existing agent repeats steps 3-8; step 2 only happens on creation.

## Prompts

OpenCode has no system-prompt flag. The template's `system-prompt.md` is written to
`~/.config/opencode/system-prompt.md` and set as `agent.build.prompt`
(`{file:...}`) in `opencode.json`, which replaces OpenCode's default
coding-assistant prompt for the primary agent: coordinators and reviewers are not
told to edit code. The template's `agents.md` goes to `~/.config/opencode/AGENTS.md`,
OpenCode's global rules file, with the start-context block on top.

## Where to change what

| I want to... | Change | Then |
|---|---|---|
| Change what a role does or says | `templates/<role>/agents.md`, `system-prompt.md` | `scripts/sync-factory.py --skip-build --skip-binary`, new agent |
| Add a reusable procedure | `skills/<name>/SKILL.md`, reference it in templates | same |
| Change models per size, provider or key | `mlflow.endpoints` / `mlflow.provider` in `asf.local.yaml`, `MODEL_PROVIDER_API_KEY` in `.env.secrets` | `make setup-services` |
| Use another gateway instead of the local MLflow | `agent_gateway` in `asf.local.yaml`, key in `.env.secrets` | `make setup-publish`, new agents |
| Add a build tool / image variant | `harness-configs/asf-opencode/tools/<name>/` (see its README) | `scripts/build-harness.py --tools <name>` |
| Add an OpenCode plugin or custom tool | `harness-configs/asf-opencode/home/.config/opencode/plugins/` (or `tool/`) | `build-harness.py --no-build`, new agent (home files are copied at creation) |
| Change how OpenCode is provisioned | `provision.py` (+ `provision_test.py`) | `build-harness.py --no-build`, new start |
| Change the OpenCode version | `OPENCODE_VERSION` in the `Dockerfile` | `make setup-images` |
| Change container env / Beads setup | `asf-entrypoint.py` (+ `asf_entrypoint_test.py`) | image rebuild |
| Mount files or set env for all agents | `~/.scion/settings.yaml` (`profiles.local.env`, `harness_overrides`, `shared_dirs`); for repeatable defaults change `scripts/scion_settings.py` | new start |
| Per-role switches | template `env:` with `ASF_*` keys (`ASF_TRACE_ROOT`), `BEADS_PREFIX` (project env), `message_mode` | new start |

Rule of thumb: the harness makes OpenCode work inside Scion; agent behaviour belongs
in templates and skills. If you are about to add instructions or policy to
`provision.py` or the entrypoint, put it in a skill or template instead.

## Debugging an agent

```sh
scion list                         # state of agents
scion logs <agent>                 # provisioning + entrypoint output (look for "asf-opencode provision:", "asf-entrypoint:")
scion attach <agent>               # the OpenCode TUI (detach: tmux prefix + d)
docker exec -it <container> bash   # inspect inside
```

Inside the container:

| Check | Command |
|---|---|
| What the agent was told | `cat ~/.config/opencode/AGENTS.md ~/.config/opencode/system-prompt.md` |
| Model provider and endpoints | `cat ~/.config/opencode/opencode.json`; `opencode models` lists `gateway/*` only |
| Effective config | `opencode debug config` |
| Gateway reachable | `curl -s -H "Authorization: Bearer $AGENT_GATEWAY_API_KEY" $AGENT_GATEWAY_URL/models` |
| OpenCode's own log | `tail ~/.local/share/opencode/log/*.log` (provider and model per request) |
| Beads connected | `env | grep -E 'BEADS|BD_'; bd list --limit 3` (in the agent's shell; `docker exec` lacks this env) |
| Caches | `echo $ASF_BUILD_CACHE` |
| Hook events reach Scion | `scion logs <agent>` shows status changes; set `SCION_HOOK_DEBUG=1` to see bridge errors |

Common causes:

- "beads.role not configured. Run 'bd init'": agents must not run `bd init` (it starts
  a private Dolt server). The entrypoint sets `beads.role` once per project.
- Agent cannot reach models: MLflow port differs from 5001, `MODEL_PROVIDER_API_KEY`
  is missing or wrong, or the provider does not serve the configured model name;
  run `make doctor`.
- Provisioning fails with "must be plain JSON": someone put comments in the agent's
  `opencode.json`; the provisioner cannot merge into it. Remove them or use
  `opencode.jsonc` for hand-written overrides (OpenCode loads it after `opencode.json`).
- Changes not visible: the agent was created before the change (home files, skills),
  or the image was not rebuilt.
- Wrong Java: the repo has no version marker and the default (25) is wrong; add
  `.java-version` to the repo or set `ASF_JAVA_VERSION` in the template, and record
  the fix with `bd remember`.

## Differences from the earlier Pi harness

Not carried over, because they were Pi extensions: the `ast_grep`/`ast_edit` agent
tools (templates and the `structural-code-search` skill now use the `ast-grep` CLI
in the image), the report guard (reminder when a turn ends without `scion message`),
the git guard, post-edit diagnostics (OpenCode has built-in LSP diagnostics), tool
output spilling and compaction checkpoints, and mapping `--thinking-level`. Port one
as an OpenCode plugin in `home/.config/opencode/plugins/` if a role turns out to need it.

## Tests

```sh
cd harness-configs/asf-opencode && python3 -m unittest provision_test asf_entrypoint_test
.venv/bin/python -m pytest tests          # setup, settings, build scripts
```

`provision_test.py` also fails when `scion_harness.py` or the upstream bridge files
differ from the submodule.
