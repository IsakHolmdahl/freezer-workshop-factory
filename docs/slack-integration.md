# Slack integration

Connect Slack to Scion: talk to agents from a Slack channel, receive their replies,
status changes and questions (`ask_user`) in Slack. Two independent options:

| Option | What it does | Effort |
|---|---|---|
| A. Slack plugin (`scion-plugin-slack`) | Two-way chat with agents, `/scion` commands, question buttons | App + plugin build + config |
| B. Notification webhook | One-way: Hub notifications (input needed, state changes) posted to a channel | One incoming webhook |

Sources (upstream, in the `scion/` submodule): plugin code `extras/scion-slack/`,
plugin loading `pkg/plugin/`, `cmd/server_foreground.go`; webhook channel
`docs-site/src/content/docs/reference/server-config.md` ("Slack channel").
Upstream has no Slack guide; this one is derived from the code and has **not been
tested end to end**. Read "Known limitations" first.

## Known limitations (read first)

- **Account linking (`/scion register`) does not work with the current Hub.** The
  plugin calls `/api/v1/slack/link` and opens `<hub>/profile/slack`, which the Hub
  does not provide (it has them for Discord, Telegram and Teams only). Without a
  linked account the plugin refuses messages and commands. Workaround: link
  manually, see step A6.
- Plain channel messages are dropped silently unless the channel has a default
  agent (`/scion default <agent>`).
- `db_path` must be an absolute path (`~` is not expanded).
- Tokens set in YAML are copied to the Hub's secret store on first start only;
  change them later in the web UI (Admin -> Integrations -> Slack) or with
  `scion hub secret set`.

---

## A. Two-way chat with the Slack plugin

Use **Socket Mode**: the plugin connects out to Slack, so no public URL is needed.

### A1. Create the Slack app

At <https://api.slack.com/apps> -> **Create New App** -> **From an app manifest**,
pick your workspace and paste:

```yaml
display_information:
  name: Scion
features:
  bot_user:
    display_name: Scion
    always_online: true
  slash_commands:
    - command: /scion
      description: Talk to Scion agents
      usage_hint: help
oauth_config:
  scopes:
    bot:
      - commands
      - chat:write
      - chat:write.customize   # replies show the agent's name
      - app_mentions:read
      - channels:history       # plain messages in public channels
      - groups:history         # plain messages in private channels
settings:
  event_subscriptions:
    bot_events:
      - app_mention
      - message.channels
      - message.groups
  interactivity:
    is_enabled: true
  socket_mode_enabled: true
```

Then:

1. **Install App** -> install to the workspace -> copy the **Bot User OAuth Token** (`xoxb-...`).
2. **Basic Information** -> **App-Level Tokens** -> **Generate Token and Scopes**,
   scope `connections:write` -> copy the token (`xapp-...`).

Note: with both `app_mention` and `message.*` subscribed, a message that @mentions
the bot in a linked channel reaches the agent twice. If that bothers you, remove
`app_mention` (plain messages then go to the channel's default agent) or remove the
`message.*` events and the `*:history` scopes (only @mentions are forwarded).

### A2. Build and install the plugin

The Hub looks for `~/.scion/plugins/broker/scion-plugin-slack`. Build it from the
submodule (needs Go, like `make all`):

```sh
mkdir -p ~/.scion/plugins/broker
cd scion/extras/scion-slack
CGO_ENABLED=0 go build -o ~/.scion/plugins/broker/scion-plugin-slack ./cmd/scion-plugin-slack
~/.scion/plugins/broker/scion-plugin-slack     # prints its config keys: build OK
```

Rebuild the plugin when you update the `scion` submodule.

### A3. Configure the plugin

Create `~/.scion/scion-slack.yaml` (non-secret settings; replace `<home>` with your
home directory, e.g. `/home/jeger`):

```yaml
socket_mode: "true"
db_path: "<home>/.scion/scion-slack.db"
```

Register it in `~/.scion/settings.yaml` (merge into the existing `server:` block):

```yaml
server:
  plugins:
    broker:
      slack:
        config_file: <home>/.scion/scion-slack.yaml
```

The Hub adds `message_broker.enabled: true` and `types: [slack]` itself, and injects
the Hub URL and credentials into the plugin; do not set `hub_url`, `hmac_key` or
`broker_id`.

### A4. Store the tokens as Hub secrets

```sh
scion hub secret set --scope hub SLACK_BOT_TOKEN xoxb-...
scion hub secret set --scope hub SLACK_APP_TOKEN xapp-...
```

Or, after A5, in the web UI: **Admin -> Integrations -> Slack** (Bot Token, App Token,
Socket Mode on), then **Restart**. Never put tokens in `.env`, templates or git.

### A5. Restart Scion and check

```sh
scion server restart                                         # local deployment
```

In the server log (`~/.scion/logs/` or `docker compose logs scion`) look for:

```
Loaded plugin ... name=slack
Injected hub credentials into broker plugin "slack"
Starting Slack Socket Mode listener
```

The web UI (**Admin -> Integrations -> Slack**) shows a health badge; healthy
details include `socket_mode` and `bot_user_id`. `scion doctor --verbose` lists it
under Platform Health.

### A6. Link your Slack user to your Scion account (workaround)

Until the Hub supports `/scion register`, add the mapping to the plugin database.
You need:

- Your Slack member ID: Slack -> your profile -> **...** -> **Copy member ID** (`U0...`).
- Your Scion e-mail: `SCION_ADMIN_EMAIL` from `.env.secrets` (the identity you use in the web UI).

```sh
python3 - <<'EOF'
import datetime, os, sqlite3
db = sqlite3.connect(os.path.expanduser("~/.scion/scion-slack.db"))
db.execute("INSERT OR REPLACE INTO user_mappings (slack_user_id, slack_username, scion_user_id, scion_email, linked_at) "
           "VALUES (?, ?, '', ?, ?)",
           ("U0XXXXXXX", "your-slack-name", "you@example.com", datetime.datetime.now(datetime.UTC).isoformat()))
db.commit()
EOF
```

The table exists once the plugin has started (A5). Repeat for each person who uses
the bot; `/scion info` in Slack shows whether you are linked.

### A7. Connect a channel to a project

In the Slack channel:

```
/invite @Scion
/scion setup                 # pick the Scion project (buttons; first 10 projects)
/scion agents                # list the project's agents
/scion default <agent>       # agent that receives plain messages, e.g. the coordinator
```

Now:

- A plain message in the channel goes to the default agent; the reply arrives in the thread.
- `/scion msg <agent> <text>` sends to a specific agent.
- `/scion status <agent>` shows an agent's state.
- When an agent asks a question (`ask_user`), a message with **Reply** / **Dismiss**
  appears; Reply opens a form, and your answer is sent to the agent (expires after 15 min).
- `/scion settings` toggles state-change notifications and observe mode
  (agent-to-agent messages); `/scion unlink` disconnects the channel; `/scion help` lists all.

Optional: set `routed_inbound_enabled: "true"` in `scion-slack.yaml` to let the Hub
route plain messages by @agent mention instead of requiring a default agent.

### A8. Agents replying to Slack

Agents reply to whoever messaged them (`scion message user:<email>` / the
conversation), and the plugin posts it in the Slack thread the message came from.
Messages from agents to the project without a specific Slack context are posted to
every channel linked to that project.

### Troubleshooting

| Symptom | Check |
|---|---|
| Plugin not in the log | `server.plugins.broker.slack` in `~/.scion/settings.yaml`; binary executable at `~/.scion/plugins/broker/scion-plugin-slack` |
| `signing_secret is required` | `socket_mode: "true"` missing in `scion-slack.yaml` |
| `cannot open` / database error | `db_path` not absolute |
| `app_token is required` | `SLACK_APP_TOKEN` secret missing; restart after setting it |
| `/scion` says "dispatch_failed" | Socket Mode off in the Slack app, or plugin not running |
| "Please use /scion register first" | Do A6 |
| Messages ignored silently | Channel not linked (`/scion setup`), or no default agent |
| Bot cannot post | Bot not invited to the channel |

---

## B. One-way notifications via incoming webhook

Posts Hub notifications (for example "agent needs input", state changes) to a
channel. No plugin, no linking.

1. In a Slack app (the one above or a new one): **Incoming Webhooks** -> on ->
   **Add New Webhook to Workspace** -> pick a channel -> copy the URL
   (`https://hooks.slack.com/services/...`).
2. Add to `~/.scion/settings.yaml`:

   ```yaml
   server:
     hub:
       notification_channels:
         - type: slack
           params:
             webhook_url: https://hooks.slack.com/services/T000/B000/XXXX
             mention_on_urgent: "@here"        # optional
           filter_urgent_only: false
           filter_types: [input-needed, state-change]
   ```

3. Restart Scion (A5). The webhook URL is a secret: keep `settings.yaml` out of git.

Details and all filter options: upstream `reference/server-config.md`, "Slack channel".
