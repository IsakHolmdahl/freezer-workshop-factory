"""Idempotently create or reconcile the AI Gateway endpoints behind one model provider.

Provider: MLFLOW_GATEWAY_PROVIDER (any MLflow AI Gateway provider: anthropic, openai,
gemini, openrouter, ollama, openai_compatible, ...), its key in MODEL_PROVIDER_API_KEY
and, for self-hosted or OpenAI-compatible providers, MLFLOW_GATEWAY_API_BASE.

Endpoints: the size tiers small, medium, large used by the agent templates (`model: small|medium|large`). Override a tier's models with
MLFLOW_ENDPOINT_<NAME>="model[:weight],model[:weight]", e.g.
MLFLOW_ENDPOINT_LARGE="claude-opus-5-5".
"""

import os
import sys

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = f"http://localhost:{os.environ.get('MLFLOW_PORT', '5001')}/api/3.0/mlflow/gateway"
# Endpoint name -> {model: traffic weight}; weights per endpoint sum to 1.0.
DEFAULT_ENDPOINTS = {
    "small": {"claude-haiku-5-5": 1.0},
    "medium": {"claude-sonnet-5-5": 1.0},
    "large": {"claude-opus-5-5": 1.0},
}
PROVIDER = os.environ.get("MLFLOW_GATEWAY_PROVIDER") or "anthropic"
API_KEY = os.environ.get("MODEL_PROVIDER_API_KEY", "")
API_BASE = os.environ.get("MLFLOW_GATEWAY_API_BASE", "")
SECRET_NAME = f"asf-{PROVIDER}"
# Providers that run without a key (the gateway still stores a placeholder secret).
KEYLESS_PROVIDERS = {"ollama"}

session = requests.Session()
# Retries connection failures while the server is still starting.
session.mount("http://", HTTPAdapter(max_retries=Retry(total=12, backoff_factor=1)))


def call(method, path, **kwargs):
    response = session.request(method, f"{BASE}/{path}", timeout=30, **kwargs)
    return response


def ensure_secret():
    """Create the provider secret, or update it so a changed key or base URL takes effect."""
    if not API_KEY and PROVIDER not in KEYLESS_PROVIDERS:
        raise RuntimeError(f"MODEL_PROVIDER_API_KEY is not set for provider {PROVIDER}; "
                           "add it to .env.secrets and run make setup")
    secret_value = {"api_key": API_KEY or "unused"}
    auth_config = {"api_base": API_BASE} if API_BASE else {}
    listed = call("GET", "secrets/list")
    listed.raise_for_status()
    for secret in listed.json().get("secrets", []):
        if secret["secret_name"] == SECRET_NAME:
            payload = {"secret_id": secret["secret_id"], "secret_value": secret_value}
            if auth_config:
                payload["auth_config"] = auth_config
            call("POST", "secrets/update", json=payload).raise_for_status()
            return secret["secret_id"]
    payload = {"secret_name": SECRET_NAME, "secret_value": secret_value, "provider": PROVIDER}
    if auth_config:
        payload["auth_config"] = auth_config
    created = call("POST", "secrets/create", json=payload)
    created.raise_for_status()
    return created.json()["secret"]["secret_id"]


def parse_models(spec):
    """'a:0.5,b:0.5' or 'a,b' (equal split) -> {model: weight}."""
    items = [item.strip() for item in spec.split(",") if item.strip()]
    if not items:
        raise ValueError(f"empty model list: {spec!r}")
    parsed = {}
    for item in items:
        name, _, weight = item.partition(":")
        parsed[name.strip()] = float(weight) if weight else None
    equal = 1.0 / len(parsed)
    return {name: equal if weight is None else weight for name, weight in parsed.items()}


def endpoints():
    configured = {}
    for name, models in DEFAULT_ENDPOINTS.items():
        override = os.environ.get(f"MLFLOW_ENDPOINT_{name.upper().replace('-', '_')}")
        configured[name] = parse_models(override) if override else models
    return configured


def ensure_model_definition(secret_id, model_name, endpoint_name):
    listed = call("GET", "model-definitions/list", params={"secret_id": secret_id})
    listed.raise_for_status()
    for definition in listed.json().get("model_definitions", []):
        if (definition["provider"] == PROVIDER and definition["model_name"] == model_name
                and definition["name"] == f"{endpoint_name}-{model_name}"):
            return definition["model_definition_id"]
    created = call(
        "POST",
        "model-definitions/create",
        json={
            "name": f"{endpoint_name}-{model_name}",
            "secret_id": secret_id,
            "provider": PROVIDER,
            "model_name": model_name,
        },
    )
    created.raise_for_status()
    return created.json()["model_definition"]["model_definition_id"]


def current_split(endpoint):
    """{model: weight}, or None when a mapping uses a definition not named after this endpoint."""
    split = {}
    for mapping in endpoint["model_mappings"]:
        definition = mapping["model_definition"]
        if definition["name"] != f"{endpoint['name']}-{definition['model_name']}":
            return None
        split[definition["model_name"]] = mapping["weight"]
    return split


def ensure_endpoint(secret_id, name, models):
    model_configs = [
        {
            "model_definition_id": ensure_model_definition(secret_id, model_name, name),
            "linkage_type": "PRIMARY",
            "weight": weight,
        }
        for model_name, weight in models.items()
    ]
    found = call("GET", "endpoints/get", params={"name": name})
    if not found.ok:
        call(
            "POST",
            "endpoints/create",
            json={"name": name, "model_configs": model_configs, "usage_tracking": True},
        ).raise_for_status()
        print(f"seed_gateway: created endpoint {name} -> {models}")
        return
    endpoint = found.json()["endpoint"]
    if current_split(endpoint) == models:
        print(f"seed_gateway: endpoint {name} already up to date")
        return
    call(
        "POST",
        "endpoints/update",
        json={"endpoint_id": endpoint["endpoint_id"], "model_configs": model_configs},
    ).raise_for_status()
    print(f"seed_gateway: updated endpoint {name} -> {models}")


def ensure_assistant_config(default_endpoint="medium"):
    """Pre-select the MLflow AI Gateway endpoint for the MLflow Assistant UI if none is selected."""
    resp = session.get(f"http://localhost:{os.environ.get('MLFLOW_PORT', '5001')}/ajax-api/3.0/mlflow/assistant/config", timeout=10)
    if not resp.ok:
        return
    providers = resp.json().get("providers", {})
    if any(isinstance(p, dict) and p.get("selected") for p in providers.values()):
        return
    put_resp = session.put(
        f"http://localhost:{os.environ.get('MLFLOW_PORT', '5001')}/ajax-api/3.0/mlflow/assistant/config",
        json={"providers": {"mlflow_gateway": {"selected": True, "model": default_endpoint}}},
        timeout=10,
    )
    if put_resp.ok:
        print(f"seed_gateway: configured MLflow Assistant -> mlflow_gateway ({default_endpoint})")


def main():
    secret_id = ensure_secret()
    failed = []
    for name, models in endpoints().items():
        try:
            ensure_endpoint(secret_id, name, models)
        except Exception as exc:  # keep seeding the other endpoints
            print(f"seed_gateway: endpoint {name} failed: {exc}", file=sys.stderr)
            failed.append(name)
    if failed:
        raise RuntimeError(f"endpoints not seeded: {', '.join(failed)}")
    try:
        ensure_assistant_config()
    except Exception as exc:
        print(f"seed_gateway: assistant config skipped: {exc}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"seed_gateway: failed: {exc}", file=sys.stderr)
        sys.exit(1)
