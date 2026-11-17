# Telemetry (Prometheus + Grafana)

The Compose stack includes an optional metrics pipeline for Scion agent telemetry:

```
Scion agents --OTLP--> otel-collector (:4317 gRPC, :4318 HTTP) --> Prometheus (:9090) --> Grafana (:3000)
```

| Service | Image | Config |
|---|---|---|
| `otel-collector` | `otel/opentelemetry-collector-contrib` | `telemetry/otel-collector.yaml` (OTLP in, Prometheus exporter on :8889; traces/logs go to the collector's debug log) |
| `run-exporter` | built from `telemetry/run-exporter/` | Reads Beads from Dolt and publishes `asf_run_*` metrics on :9105 (see [Runs](#runs-compare-coordinator-tasks)) |
| `prometheus` | `prom/prometheus` | `telemetry/prometheus.yml` (scrapes the collector and the run exporter) |
| `grafana` | `grafana/grafana` | `telemetry/grafana/provisioning/` (datasource + dashboard provider), `telemetry/grafana/dashboards/` |

All ports bind to `127.0.0.1`. Override with `OTEL_GRPC_PORT`, `OTEL_HTTP_PORT`,
`PROMETHEUS_PORT`, `GRAFANA_PORT`, `PROMETHEUS_RETENTION`, `GRAFANA_ADMIN_USER`,
`GRAFANA_ADMIN_PASSWORD` in `.env` (Grafana defaults to `admin`/`admin`; change it for anything non-local).

## Start the stack

```bash
make start    # all services, rebuilt when changed, then the Hub and broker
```

Only the telemetry services: `docker compose up -d --build otel-collector run-exporter prometheus grafana`.

## Enable telemetry in Scion

Telemetry is enabled by default via `asf.yaml` (`telemetry.enabled: true`, `otlp_endpoint: localhost:4317`). `./setup.py` writes the block below into `~/.scion/settings.yaml` (and `deploy/scion/settings.docker.yaml` has the same defaults). The `scion` service and agent containers use host networking, so the collector is reachable at `localhost`:

```yaml
telemetry:
    enabled: true
    cloud:
        enabled: true
        endpoint: localhost:4317   # OTLP gRPC; use http://localhost:4318 for HTTP
        protocol: grpc
        tls:
            enabled: false
```

Restart the `scion` service and start new agents afterwards. Field names under
`cloud:` follow upstream Scion's telemetry settings; verify them against the
Scion version you run (`SCION_SOURCE_REF`) if no data arrives. Harnesses with a
native OTEL emitter export directly; for OpenCode (`asf-opencode`), which has none, token usage is
forwarded from model-end hooks (see `harness-configs/asf-opencode/config.yaml`).

## View the dashboard

1. Open <http://localhost:3000> and log in.
2. Open **Dashboards → Scion → Scion Agent Telemetry**. It is provisioned automatically
   (edit `telemetry/grafana/dashboards/scion-agent-telemetry.json`; changes reload within ~30s).
3. Raw metrics: <http://localhost:9090> (Prometheus). Check **Status → Targets** shows `otel-collector` UP.

Panels query the metrics emitted by `sciontool`:
`scion_usage_tokens_total{token_type, scion_agent_slug, model}`,
`gen_ai_api_calls_total{scion_agent_slug, model, status}`, and
`agent_tool_calls_total{scion_agent_slug, tool_name, status}`
(enabled for `asf-opencode` by `SCION_USAGE_SOURCE=hooks`).

## Runs: compare coordinator tasks

A **run** is one epic in Beads: one task given to a coordinator. Agent metrics carry
only the agent name, so `run-exporter` publishes which agent belonged to which epic
and Grafana joins the two. Open **Dashboards → Scion → ASF Run Comparison**, pick the
runs to compare in the **Run** dropdown and set the time range wide enough to cover
all of them: token and call totals are the last values seen inside that range.

| Metric | Meaning |
|---|---|
| `asf_run_info{project, run, title, status, coordinator}` | One series per epic (`run` is the epic id) |
| `asf_run_agent_info{project, run, scion_agent_slug, role, bead}` | Agent that worked in the run; join key for the `sciontool` metrics |
| `asf_run_duration_seconds` | Epic created to closed (to now while it is open) |
| `asf_run_beads{type, status}` | Beads under the epic |
| `asf_run_labelled_beads{label}` | Beads labelled `rework`, `friction`, `agent-failed`, `user-interjection` or `escalation` |
| `asf_run_agents{role}` | Agents per role |
| `asf_run_created_timestamp_seconds`, `asf_run_closed`, `asf_run_exporter_up` | Start time, closed flag, exporter health |

Group any agent metric per run with:

```promql
sum by (run) (
  sum by (scion_agent_slug) (max_over_time(scion_usage_tokens_total[$__range]))
  * on (scion_agent_slug) group_left(run, role)
  max by (scion_agent_slug, run, role) (asf_run_agent_info)
)
```

How agents are matched (`telemetry/run-exporter/runs.py`):

- The coordinator is the epic's assignee, else its creator. Start a new coordinator
  for each run: an agent that appears in several epics counts for the newest only.
- A worker is a bead's assignee, or anyone who acted on the bead under a name that
  starts with the bead id (`<bead-id>-<topic>-<role>`), so failed attempts count.
- The role is the name's suffix, matched against the directories in `templates/`.
- Epics older than `RUN_EXPORTER_MAX_AGE_DAYS` (default 30) are left out.
