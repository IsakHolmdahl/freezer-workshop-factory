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
| Provider (`anthropic`, `openai`, `gemini`, `openrouter`, `openai_compatible`, `ollama`, ...) | `asf.yaml` `mlflow.provider` | `MLFLOW_GATEWAY_PROVIDER` |
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

## Trace sessions

`bootstrap/gateway_session.py` maps the request headers `X-MLflow-Session-Id` and
`X-MLflow-Trace-User` to the trace metadata `mlflow.trace.session` and
`mlflow.trace.user` for every AI Gateway call. The OpenCode harness sends them
(orchestration trace ID and agent name). Invalid values are ignored and never
fail a request. `bootstrap/sitecustomize.py` installs this in every MLflow Python
worker through `PYTHONPATH`. Test with the MLflow image:
`python -m unittest test_gateway_session.py` (bootstrap on `PYTHONPATH`).
