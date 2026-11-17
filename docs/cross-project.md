# Cross-project delegation

A coordinator works in one Scion project (usually one repository). When an epic also
needs changes in another repository, the coordinator hands that part to the
coordinator of the other project, which plans and runs it there and reports back.
This document explains how that works, how to enable it and how to limit which
projects can be reached.

## How it works

```
project A                                                  project B
coordinator ── scion message --project B @coordinator ──▶ coordinator ──▶ workers in B
     ▲                                                         │
     └──── "DONE <A's bead-id>" --project A @coordinator ◀─────┘
```

- **Messages, not agents.** Scion forbids an agent from creating agents in another
  project ("Agents can only create sub-agents within their own project",
  `scion/pkg/hub/authorize.go`). So each participating project needs a receiving
  agent, started by a human (or by `scion cross-project apply --start`), and
  work is delegated to it by message.
- **Addressing:** `scion message --non-interactive --project <project-slug> @<agent> ...`
  (never `@<project-slug>/<agent>`, which Scion parses as a same-project agent name
  and fails with `404 Agent route not found`). The brief is self-contained (the other
  project cannot read your Beads) and names the completion command.
- **Agents learn the allowed peers** from the project variable `ASF_PEER_PROJECTS`
  (`slug=agent,...`), set only on selected projects; agents cannot list projects
  themselves. It is a hint: Scion's checks still decide every send.
- **Behaviour** is defined in the `cross-project-delegation` skill (attached to the
  `coordinator` template) and two lines in `templates/coordinator/agents.md`.

Scion checks three things for every cross-project message
(`scion/docs-site/src/content/docs/reference/messaging-authorization.md`):

| Check | Where | Set by |
|---|---|---|
| Hub switch `cross_project_messaging_enabled` (default off) | Hub | `cross-project.py` |
| Sender's message mode is `hub` (coordinators: `message_mode: hub` in the template) | agent | template / `--message-mode hub` |
| Destination project's inbound policy: `none` (default), `members`, `any` | each project | `cross-project.py` |

`members` accepts messages from agents whose originating user is a member of the
destination project; `any` from every agent on the Hub. On a single-user
workstation both behave the same.

## Enable it

Run the installed ASF shim from any directory:

```sh
scion cross-project setup
```

It displays a numbered list of projects on the Hub. Enter numbers such as `1 3`
or `1,3` to select projects, or `-1` to connect all listed projects (at least two
are needed). Then confirm whether to start a receiving coordinator where one
is missing. Enter accepts the saved selection; `none` clears it. This replaces
the cross-project task in the main installer.

You can also select by slug prefix and exclude numbered entries:

| Input | Selection |
|---|---|
| `prefix:team-` | All slugs starting with `team-` (case-sensitive) |
| `prefix:team- prefix:service-` | Projects matching either prefix |
| `-1 except 2 5` | All projects except numbers 2 and 5 |
| `except 2,5` | Same as above |
| `prefix:team- except 3` | Prefix matches excluding number 3 |
| `1 2 3 except 2` | Numbers 1 and 3 |

Numbers always refer to the displayed list. Invalid selections or filters with no
matches prompt again; use `none` explicitly to disable managed connections.
The shell command stores its
selection in `$SCION_HOME/asf-cross-project-config.json` (default `~/.scion/`):

```json
{
  "peers": {
    "ai-software-factory": "coordinator",
    "order-service-api": "coordinator"
  },
  "inbound": "members"
}
```

and applies it immediately. For non-interactive setup, specify projects explicitly:

```sh
scion cross-project setup --project alpha --project beta --start
scion cross-project setup --project alpha --project beta --inbound members
scion cross-project apply --start
scion cross-project status
scion cross-project disable
```

With no subcommand, `scion cross-project` opens setup. `apply` reapplies the saved
configuration. The shim and both extensions run in POSIX `sh`, using `jq` for
JSON. They are installed together and need no Python or ASF checkout at runtime.
Run as the Hub admin, with Scion running. `--help` describes the options.

`apply`:

1. Turns on the Hub switch while at least one project takes part.
2. Sets the selected projects' inbound policy to `inbound`. Projects the script
   opened before and that are no longer selected go back to `none`. Projects it
   never managed keep their owner's setting (`status` shows them), as Scion
   intends: the inbound policy belongs to the project owner.
3. Sets `ASF_PEER_PROJECTS` as a **project** variable on the selected projects only,
   so only their agents learn the peers.
4. With `--start`, starts the receiving agent (coordinator template, hub message
   mode) where it is missing.

Which projects it manages is remembered in `~/.scion/asf-cross-project.json`. Every
project must be selected to *receive*, so selecting both sides also lets replies
through. Then recreate running coordinators so they get the skill and the peer list.

## Limit the scope

- **Only selected projects are opened** by this setup. Deselect a project and run
   `scion cross-project setup` (or `apply` after editing configuration) to close it again.
- **Who may send:** keep `inbound: members` and only add the right people to each
  project. With `any`, every `hub`-mode agent on the Hub can send to the selected
  projects.
- **Which agents can send:** only agents in `hub` message mode, i.e. coordinators.
  Workers use `project` mode and cannot reach other projects. Check with
  `scion whoami --full` in the agent; change with `scion set-message-mode <agent> project`.
- **What the receiver does:** the `cross-project-delegation` skill treats delegated work
  as a request: only work inside its own project, no secrets, no settings or
  permission changes, human approval for large or unclear work, otherwise `DECLINED`.
  Scion's boundaries cover delivery and access, not what the receiver does with a
  message, so this rule is the important one.
- **Switch it off:** enter `none` in `scion cross-project setup`, or run
  `scion cross-project disable`. The
  Hub switch stays on only if a project the script does not manage still accepts
  messages.

What Scion does not offer (as of the pinned submodule):

- No per-sender allow-list: the inbound policy is per receiving project and applies
  to all eligible senders.
- Policies are directional: B accepting from A says nothing about A accepting from B.
- Only direct messages cross projects; group rooms and broadcasts do not.
- Agents cannot start agents in other projects at all.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `message_denied ... cross_project_disabled` | Hub switch off: `scion cross-project apply` |
| `... cross_project_inbound_none` / denied for a project | Project not in the saved `peers` selection, apply not run |
| `... sender mode` | Sender not in `hub` mode: it must use the coordinator template or `scion set-message-mode <agent> hub` (by a human) |
| No reply from the other side | Sender's own project not listed (cannot receive), or receiving agent missing: `scion cross-project status` |
| Coordinator does not know peers | Created before `apply`, or its project is not selected; recreate it (`ASF_PEER_PROJECTS` is read at start) |

## Files

| File | Role |
|---|---|
| `scion cross-project setup` | Project selection, saves JSON configuration, applies communication settings |
| `~/.scion/asf-cross-project-config.json` | Selected peers and inbound policy; edit receiver names here if needed |
| `scripts/scion-cross-project.sh` | Shell implementation of setup, apply, status and disable |
| `~/.scion/asf-cross-project.json` | Projects the script manages (so it never closes others) |
| `skills/cross-project-delegation/SKILL.md` | How coordinators delegate and answer delegated work |
| `templates/coordinator/` | `message_mode: hub`, routing line, skill reference |

The earlier `scripts/cross-project.py` and its `asf.yaml` settings are legacy
standalone tools. The shim uses its JSON configuration; run `scion cross-project
setup` once to select peers when upgrading. The managed-project state is shared,
so previously managed projects can still be closed safely.
