# ASF OpenCode harness (MLflow AI Gateway)

Scion harness-config for the [OpenCode](https://opencode.ai) coding agent, with
models served through the MLflow AI Gateway and Beads/Dolt for work tracking.
Built on upstream `scion/harnesses/opencode`; read the upstream authoring guide
(`scion/harnesses/authoring-guide.md`) before changing it. How an agent starts:
[docs/opencode-harness.md](../../docs/opencode-harness.md).

What differs from upstream:

| | upstream opencode | this harness |
|---|---|---|
| Models | `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`, `auth.json`, Vertex AI | One OpenAI-compatible agent gateway (`AGENT_GATEWAY_*`, provider `gateway`), models `small`/`medium`/`large`; default the local MLflow AI Gateway. All other providers are disabled |
| Size aliases | not in `provider/model` form, skipped | `gateway/small|medium|large` |
| System prompt | prepended to `AGENTS.md` | replaces the primary agent's prompt (`agent.build.prompt`), so non-coding roles are not told to edit code |
| Permissions | OpenCode defaults | everything allowed: nobody answers a prompt in an agent container |
| Lifecycle bridge | `scion-bridge.js` | the same file, plus `asf-reminders.js` (cancels stale `scion schedule` reminders) |
| Image | OpenCode only | pinned OpenCode + `bd`, `dolt`, `rtk`, `fd`, `ripgrep`, `ast-grep` |
| Entrypoint | base image | `asf-entrypoint.py`: Beads database/env, shared MLflow trace ID, Gradle/pnpm/pip caches, start context in `AGENTS.md` |

Agent behaviour (messaging, output style, Beads workflow) is not part of the
harness: it lives in `templates/*/agents.md` and `skills/` (for example
`output-discipline`, `beads-orchestration`). See `docs/agent-roles.md`.

## Files

| File | Purpose |
|---|---|
| `config.yaml` | Harness definition: command, model aliases, capabilities, auth |
| `provision.py` | Pre-start provisioner: merges the gateway provider, model, permissions, system prompt and MCP servers into `opencode.json`; writes `AGENTS.md` |
| `scion_harness.py` | Exact copy of `scion/harnesses/scion_harness.py`; do not edit (a test checks it) |
| `dialect.yaml` | Upstream copy: maps `scion-bridge.js` events to Scion's normalized hook events |
| `agent-plugins.yaml` | Agent Plugins: not supported |
| `home/.config/opencode/plugins/scion-bridge.js` | Upstream copy: lifecycle, tool and usage events to `sciontool hook` |
| `home/.config/opencode/plugins/asf-reminders.js` | Ours: cancels the agent's pending reminders on wake-up and on `task_completed` |
| `home/.local/bin/opencode-launch.sh` | Upstream copy: hides `GITHUB_TOKEN` from OpenCode, keeps git credentials working |
| `home/.config/opencode/opencode.json` | Base config the provisioner merges into |
| `asf-entrypoint.py` | Image entrypoint (after `sciontool init`) |
| `Dockerfile`, `image/`, `tools/` | Image build inputs; not part of the installed bundle |

`home/` is copied into an agent's home when the agent is **created**; the rest of
the bundle is staged on every start.

## Build and install

`make setup` (repo root) builds and installs everything. By hand:

```sh
python3 scripts/build-scion-base-image.py            # scion-local-base:latest
python3 scripts/build-harness.py --base-image scion-local-base:latest --tools pnpm,python
```

`build-harness.py` builds `asf-opencode` plus the tool layers in `tools/`
(`pnpm`, `python`) and installs the matching harness-config
(`asf-opencode`, `asf-opencode-pnpm-python`, ...).

## Updating from upstream

After moving the `scion` submodule:

```sh
cp scion/harnesses/scion_harness.py harness-configs/asf-opencode/
cp scion/harnesses/opencode/dialect.yaml harness-configs/asf-opencode/
cp scion/harnesses/opencode/home/.config/opencode/plugins/scion-bridge.js \
   harness-configs/asf-opencode/home/.config/opencode/plugins/
cp scion/harnesses/opencode/home/.local/bin/opencode-launch.sh \
   harness-configs/asf-opencode/home/.local/bin/
cd harness-configs/asf-opencode && python3 -m unittest provision_test asf_entrypoint_test
```

Then compare `scion/harnesses/opencode/config.yaml` and `provision.py` with ours
for new keys (last compared at `scion` commit `8845a9407`).

## Tests

```sh
python3 -m unittest provision_test asf_entrypoint_test
```
