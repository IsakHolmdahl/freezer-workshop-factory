# MCP integration: reference for future ASF implementation

Findings checked against the Scion submodule on 2026-11-04; harness section updated for OpenCode.
The examples below are implementation starting points, not deployed integrations.

Implementation draft for the Atlassian integration:
[Scion → FastMCP → Confluence/Jira](../.design/scion-fastmcp-atlassian.md).

## What Scion's MCP functionality does

Model Context Protocol (MCP) gives coding agents access to tools and data exposed
by external services. Scion configures those connections for the chosen harness;
the harness, such as OpenCode or Claude Code, is the actual MCP client.

```text
Template scion-agent.yaml: mcp_servers
                  ↓
Scion stages the server definitions for the agent
                  ↓
Harness provisioner translates them into native configuration
                  ↓
OpenCode / Claude Code / another harness acts as MCP client
                  ↓
MCP server exposes tools and data
```

Scion's MCP functionality does not expose Scion agent-management operations as an
MCP server. External applications integrate with Scion through its CLI, REST API
or A2A bridge. There is also no central MCP gateway in this provisioning path:
the agent's harness connects to the configured server directly.

### Process and connection ownership

| Transport | How it runs |
|---|---|
| `stdio` | The harness starts the MCP server as a child process inside the agent container and communicates through stdin/stdout. The command and its dependencies must be available in that container. |
| `streamable-http` | The harness connects to an existing MCP endpoint. That service must be deployed separately and reachable from the agent container. |
| `sse` | Accepted by Scion's universal schema, but support depends on the harness. ASF OpenCode maps it to a `remote` entry. |

Scion's `services` configuration is separate from `mcp_servers`. Defining an HTTP
MCP endpoint does not start its service or establish readiness dependencies.

## Useful ASF integrations

| Integration | Example agent use case |
|---|---|
| Browser / Chrome DevTools | A tester inspects a running app, exercises a user journey and gathers evidence for UI defects. |
| SharePoint / Confluence | An architect or developer retrieves company standards, architecture decisions and service documentation. |
| Azure DevOps / Jira | A coordinator reads requirements, updates work items and links implementation results. |
| Database | A developer or tester inspects schemas and queries development/test data. |
| Internal company APIs | An investigator looks up service ownership, deployment status or API contracts. |

These are candidate integrations, not claims that ASF includes servers for them.
Browser verification and internal documentation access are useful initial targets.
For capabilities already well served by installed CLIs (`git`, `gh`, `bd`), assess
whether MCP offers a concrete benefit before adding another integration.

Scion's main benefit is declaring a connection once for a role and translating it
for different harnesses. For example, a tester role can receive browser tooling
without maintaining separate OpenCode and Claude configuration files. Actual transport
and feature support still needs checking for each harness.

## Configuration starting points

Declare universal MCP server definitions under `mcp_servers` in the relevant
template's `scion-agent.yaml`. See [templates-and-skills.md](templates-and-skills.md)
for template conventions and publishing.

### Local stdio server

```yaml
# Illustrative fragment in templates/<role>/scion-agent.yaml.
# This executable must first be installed in the selected agent image.
mcp_servers:
  internal-docs:
    transport: stdio
    command: internal-docs-mcp
    args: ["--collection", "engineering"]
```

The command above is a placeholder for a selected implementation, not a package
provided by ASF. Prefer an established server for the target system before
building a custom one. Add required executables/dependencies through the image
and tool-layer mechanisms described in [opencode-harness.md](opencode-harness.md).

### Remote streamable HTTP server

```yaml
# Illustrative endpoint; deploy the service separately.
mcp_servers:
  internal-docs:
    transport: streamable-http
    url: https://mcp.example.com/docs
```

Scion's universal schema also provides `env` for stdio servers and `headers` for
HTTP servers. Choose the server's authentication method and verify credential
delivery and refresh before implementation. Keep credential values in the
deployment's secret mechanism, not committed template files. Do not assume that
template environment interpolation, interactive OAuth or automatic token refresh
is provided by the MCP provisioning helpers.

## Current ASF OpenCode implementation

Our harness is `asf-opencode` and its tool variants; see [opencode-harness.md](opencode-harness.md).

- Scion stages the resolved definitions in `inputs/mcp-servers.json` in the
  agent's harness bundle.
- `harness-configs/asf-opencode/provision.py` translates them into the `mcp` map of
  `~/.config/opencode/opencode.json`: `stdio` becomes a `local` entry (one
  `command` array, `environment`), `sse` and `streamable-http` become `remote`
  entries (`url`, `headers`). This is the same mapping as upstream
  `scion/harnesses/opencode`.
- OpenCode's built-in MCP client consumes that configuration.
- Project-scoped MCP definitions are installed at user level within the agent
  home; OpenCode does not distinguish project-scoped MCP.
- The writer merges configured entries into the existing map. Verify removal
  behaviour when changing or retiring servers in an existing agent home.
- The file is written owner-only, because entries can carry tokens.

MCP configuration can also arrive through Scion Agent Plugins, which have a
separate manifest and harness-support mechanism. Our current ASF OpenCode harness
declares Agent Plugins unsupported; use the template `mcp_servers` path for an
initial implementation.

## Implementation and verification sequence

1. Pick a concrete role and task, such as browser verification by a tester.
2. Select an existing MCP server and verify its tools, transport, authentication
   and compatibility with OpenCode's client.
3. Make the stdio command available in the agent image, or deploy and verify the
   remote HTTP endpoint from the agent network.
4. Add the role's `mcp_servers` definition and arrange the required credentials.
5. Publish the template and rebuild/publish the image if dependencies changed.
   Start a fresh agent with the intended `asf-opencode-*` variant.
6. Confirm the native MCP configuration, server startup/connection and tool
   discovery. Run a real task and verify both its result and useful diagnostics
   when the server is unavailable or authentication expires.

## References

- [Scion templates](https://googlecloudplatform.github.io/scion/local/templates/) — universal `mcp_servers` declarations
- [Scion agent configuration](https://googlecloudplatform.github.io/scion/reference/agent-config/)
- [Scion supported harnesses](https://googlecloudplatform.github.io/scion/supported-harnesses/) — per-harness MCP capabilities
- [Scion Agent Plugins](https://googlecloudplatform.github.io/scion/local/agent-plugins/)
- `scion/pkg/api/types.go`: universal MCP schema and validation
- `scion/harnesses/scion_harness.py`: `read_mcp_servers`,
  `apply_mcp_servers_simple`, `apply_mcp_translated`
- `harness-configs/asf-opencode/provision.py`: ASF OpenCode translation and native config writer
- `scion/.design/template-mcp-servers.md`: design background; verify details
  against current code before implementation
