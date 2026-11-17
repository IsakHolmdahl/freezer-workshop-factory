# Agent roles: waiting, routing and completion

How the role templates in `templates/` are expected to behave, and how to check
a change. Harness mechanics are in `harness-configs/asf-opencode/README.md`.

Check template files and required coordination rules:

```sh
python3 -B scripts/check-instruction-budgets.py
```

## Waiting

All role templates require Scion-managed waiting: an agent with no independent
work sets `sciontool status blocked "<reason>"` and ends its turn. Scion handles
lifecycle monitoring and notifications. Agents must not sleep, poll lifecycle
state, schedule startup checks, or run recurring status sweeps. Setting blocked
status alone does not stop an executing shell loop; the agent must yield.

## Applying and checking role changes

Sync the changed templates (`./setup.py` does this for all of them):

```sh
scion template sync coordinator
scion template sync developer
```

When updating the shared waiting rule across roles, sync all changed templates
with `scion template sync` from the Scion config home.

Start a fresh coordinator with the synced template and `--harness-config asf-opencode`.
Templates do not choose a harness; see [templates-and-skills.md](templates-and-skills.md)
for settings defaults and explicit stack selection.
Existing sessions retain their conversation history and may use older staged
configuration. These prompt/provisioner changes do not require an image rebuild.

Inside the new agent, check that `~/.config/opencode/AGENTS.md` contains the coordinator
instructions and that the launch command uses `--system-prompt`. For a behavioral
smoke test, request a feature implementation. The coordinator should decompose the
feature into multiple task beads for a team of agents (never a single developer),
write the briefs into beads and/or `/scion-volumes/scratchpad/`, start the parallel
workers with `--harness-config asf-opencode`, and wait via Scion
notifications, setting blocked status and ending its turn. It should not edit
the code itself, delegate a feature to a single developer, or issue sleeps, delayed
startup checks, or periodic lifecycle polls. Prompt configuration is role guidance,
not a filesystem or shell sandbox.

## Completion reporting

Every role now requires a verified dispatcher handoff before `task_completed`.
Bead briefs must identify the dispatcher separately from the human question contact:

```text
Dispatcher: coord1
Completion recipient: agent:coord1
Direct contact for product questions: <originating user conversation>
```

For example, a reviewer sends its concise verdict/report reference using
`scion message --non-interactive agent:coord1 --body-file <summary-path>` and checks
the command result. A bare email such as `jeger@localhost` is parsed as an agent
name, not a human DM. A failed send must be corrected and retried, or recorded as
a delivery blocker with the pending report preserved. Inline output and lifecycle
events do not substitute for the dispatcher message.

Smoke-test both a clean review and a re-review: each should send a verdict to the
dispatcher, then signal task completion. In an isolated test with an invalid
completion address, the worker should report blocked rather than claiming the
handoff succeeded. Template changes require installation/sync and fresh agent
sessions; they do not repair a gateway delivery error by themselves.


## Pinned process skill

The coordinator uses the local Hub's
`skill://scion/global/software-engineering-process@3.0.0`, not the upstream GitHub
copy. From the repository root run:

```sh
python3 -B scripts/sync-process-skill.py --check-local
python3 -B scripts/sync-process-skill.py
```

The first command checks all local skill file hashes and consuming template pins.
The second publishes the version if absent and verifies the Hub's resolved file
hashes against `skills/software-engineering-process.lock.json`. It fails on drift
rather than silently reusing an older release. Sync the updated template
after this succeeds. When changing the skill, publish a new version and update
the lock and template pin together. The generic publishing script's default
version is not a substitute for this release check.

For a fresh agent, inspect `~/.scion/resolved-skills.json`: the process-skill URI
and `resolvedVersion` must match the lock. Existing sessions retain staged skills.
To check the manifest and actual installed bytes from the host:

```sh
python3 -B scripts/sync-process-skill.py --agent-home /path/to/agent/home
```

The Scion server entrypoint invokes the pinned-skill check before its general
skill publication step. Run it manually when deploying through a host CLI.
Use `--no-hub` for the harness install step above: with Hub routing enabled,
`install` may upload directly without refreshing the local copy, and a subsequent
`sync` would then upload the stale local bundle.
