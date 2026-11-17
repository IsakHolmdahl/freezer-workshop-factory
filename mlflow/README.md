# MLflow AI Gateway: the model router

The `mlflow` compose service runs MLflow with its AI Gateway. Agents send every
model request to it (`http://localhost:5001/gateway/mlflow/v1`, OpenAI-compatible)
and name an endpoint: `small`, `medium` or `large`. The gateway forwards the
request to the configured provider model and records a trace.

## Provider and endpoints

`bootstrap/seed_gateway.py` runs at container start and from
`make setup-services`. It is idempotent: it creates or updates

- one gateway secret `asf-<provider>` holding `MODEL_PROVIDER_API_KEY`
  (from `.env.secrets`) and, when set, the API base URL;
- one endpoint per size, mapped to the models in `MLFLOW_ENDPOINT_<SIZE>`.

| Setting | Source | Container env |
|---|---|---|
| Provider (`anthropic`, `openai`, `gemini`, `openrouter`, `openai_compatible`, `ollama`, `github_copilot`, ...) | `asf.yaml` `mlflow.provider` | `MLFLOW_GATEWAY_PROVIDER` |
| API base (OpenAI-compatible servers, Ollama, proxies) | `asf.yaml` `mlflow.api_base` | `MLFLOW_GATEWAY_API_BASE` |
| Models per endpoint (`model` or `a:0.5,b:0.5` to split traffic) | `asf.yaml` `mlflow.endpoints` | `MLFLOW_ENDPOINT_SMALL|MEDIUM|LARGE` |
| API key | `.env.secrets` `MODEL_PROVIDER_API_KEY` | same |

Change them in `asf.local.yaml` / `.env.secrets`, then run `make setup-services`
(see [docs/setup-steps.md](../docs/setup-steps.md#model-provider)). Check by hand:

```sh
docker exec mlflow-server python /opt/mlflow-bootstrap/seed_gateway.py
curl -s http://localhost:5001/gateway/mlflow/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model": "small", "max_tokens": 5, "messages": [{"role": "user", "content": "Reply with: ok"}]}'
```

The MLflow UI (http://localhost:5001) shows the endpoints, usage and traces.

## GitHub Copilot

With `mlflow.provider: github_copilot` LiteLLM authenticates with a GitHub OAuth
login kept in the `copilot-token` volume (`/var/lib/litellm-copilot`); no API key is
used. `make setup-copilot` stores one (browser device login, pasted token or an
editor's Copilot login; see [setup-steps.md](../docs/setup-steps.md#github-copilot)).
At start `bootstrap/entrypoint.sh` refreshes the Copilot API key, or, without a
login, starts a device login in the background that prints a code to
`docker logs mlflow-server`.

**System prompts.** LiteLLM's Copilot adapter turns `system` messages into
`assistant` history by default, which downgrades the role and workflow
instructions agents send, even though the incoming MLflow trace shows a correct
system prompt. `bootstrap/sitecustomize.py` sets
`litellm.disable_copilot_system_to_assistant = True` in every Python interpreter
(fresh gateway workers too) and keeps `reasoning_effort` for Copilot Claude and
Gemini models, which LiteLLM would otherwise drop. The image build
(`bootstrap/verify_runtime.py`) and the compose command fail if the patches stop
applying. Check without a model call:

```sh
docker exec mlflow-server python /opt/mlflow-bootstrap/verify_runtime.py
docker cp mlflow/test_system_roles.py mlflow-server:/tmp/ && \
  docker exec mlflow-server python -m unittest /tmp/test_system_roles.py
```

A container created before this change lacks the mount and the token volume:
recreate it with `docker compose up -d --force-recreate mlflow`.

## Trace sessions

`bootstrap/gateway_session.py` maps the request headers `X-MLflow-Session-Id` and
`X-MLflow-Trace-User` to the trace metadata `mlflow.trace.session` and
`mlflow.trace.user` for every AI Gateway call. The OpenCode harness sends them
(orchestration trace ID and agent name). Invalid values are ignored and never
fail a request. `bootstrap/sitecustomize.py` installs this in every MLflow Python
worker through `PYTHONPATH`. Test with the MLflow image:
`python -m unittest test_gateway_session.py` (bootstrap on `PYTHONPATH`).
