#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""OpenCode container-side provisioner (pre-start) for the ASF agent gateway.

Differences from the upstream opencode harness (scion/harnesses/opencode/provision.py):
- Models come from one OpenAI-compatible agent gateway (provider "gateway" in
  opencode.json), configured with AGENT_GATEWAY_URL, AGENT_GATEWAY_API_KEY and
  AGENT_GATEWAY_TYPE (Hub variables written by ./setup.py from asf.yaml).
  Type "mlflow" (the default local MLflow AI Gateway) adds endpoint discovery
  and MLflow trace headers.
- The template system prompt replaces OpenCode's default prompt for the primary
  agent (agent.build.prompt), which conflicts with coordinating/review roles.
- No provider logins: auth files, Vertex AI and GitHub Copilot are not used.

Agent behaviour (messaging, output style, Beads workflow) belongs in templates
and skills, not here.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import sys
import urllib.request
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scion_harness as sh  # noqa: E402

assert sh.INTERFACE_VERSION >= 3, (
    "asf-opencode provision.py requires scion_harness INTERFACE_VERSION >= 3; "
    f"got {sh.INTERFACE_VERSION}. Copy scion/harnesses/scion_harness.py."
)

DEFAULT_CONFIG_DIR = ".config/opencode"
CONFIG_SCHEMA = "https://opencode.ai/config.json"
PROVIDER = "gateway"
PRIMARY_AGENT = "build"
# Default local MLflow AI Gateway (asf.yaml mlflow.port 5001).
DEFAULT_GATEWAY_URL = "http://localhost:5001/gateway/mlflow/v1"
DEFAULT_GATEWAY_TYPE = "mlflow"
MLFLOW_API_SUFFIX = "/gateway/mlflow/v1"
DEFAULT_MODELS = ("small", "medium", "large")
# Sent only to an MLflow gateway: groups one orchestration into one trace session.
# {env:...} is expanded by OpenCode when it loads the config; asf-entrypoint sets TRACE_ID.
MLFLOW_TRACE_HEADERS = {
    "X-MLflow-Session-Id": "{env:TRACE_ID}",
    "X-MLflow-Trace-User": "{env:SCION_AGENT_NAME}",
    "X-MLflow-Gateway-Caller": "opencode",
}
# Providers OpenCode would otherwise pick up from stray credentials in the container.
DISABLED_PROVIDERS = ["github-copilot", "opencode"]
# Nobody answers a permission prompt in an agent container.
PERMISSIONS = {"edit": "allow", "bash": "allow", "webfetch": "allow",
               "doom_loop": "allow", "external_directory": "allow"}

AUTH = sh.AuthSpec(
    "asf-opencode",
    [sh.env_method("api-key", any_of=["AGENT_GATEWAY_API_KEY"], hint="set AGENT_GATEWAY_API_KEY",
                   env_fallback=True)],
    fallback_to_none_on_error=True,
)


def _config_dir(ctx: sh.ProvisionContext) -> str:
    return os.path.join(ctx.home, str(ctx.harness_config.get("config_dir") or DEFAULT_CONFIG_DIR))


def _config_path(ctx: sh.ProvisionContext) -> str:
    return os.path.join(_config_dir(ctx), "opencode.json")


def _load_config(ctx: sh.ProvisionContext) -> dict[str, Any]:
    """opencode.json as a dict; {} when missing. A file OpenCode accepts but json
    rejects (comments) cannot be merged safely, so it is an error here."""
    path = _config_path(ctx)
    if not os.path.isfile(path):
        return {}
    try:
        data = sh.load_json(path)
    except (OSError, ValueError) as exc:
        raise sh.ProvisionError(f"{path} must be plain JSON (no comments): {exc}") from exc
    if not isinstance(data, dict):
        raise sh.ProvisionError(f"{path} must contain a JSON object")
    return data


def _block(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    if not isinstance(value, dict):
        value = {}
        config[key] = value
    return value


# ---------------------------------------------------------------------------
# Agent gateway
# ---------------------------------------------------------------------------

def _gateway_url(ctx: sh.ProvisionContext, *, keyless: bool) -> str:
    url = (ctx.read_secret("AGENT_GATEWAY_URL", env_fallback=True) or DEFAULT_GATEWAY_URL).strip()
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise sh.ProvisionError("AGENT_GATEWAY_URL must be a valid HTTP(S) URL") from exc
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.query
            or parsed.fragment or parsed.username is not None or parsed.password is not None or port == 0):
        raise sh.ProvisionError(
            "AGENT_GATEWAY_URL must be an HTTP(S) URL without credentials, query, or fragment")
    host = parsed.hostname.lower().rstrip(".")
    local = host == "localhost"
    if not local:
        with contextlib.suppress(ValueError):
            local = ipaddress.ip_address(host).is_loopback
    if not local and parsed.scheme != "https":
        raise sh.ProvisionError("Non-local AGENT_GATEWAY_URL must use HTTPS")
    if keyless and not local:
        raise sh.ProvisionError("AGENT_GATEWAY_API_KEY is required for a non-local gateway")
    return url.rstrip("/")


def _gateway_type(ctx: sh.ProvisionContext) -> str:
    return (ctx.read_secret("AGENT_GATEWAY_TYPE", env_fallback=True) or DEFAULT_GATEWAY_TYPE).strip().lower()



def _context_window(ctx: sh.ProvisionContext) -> int | None:
    """Configured context window in tokens (default 128000; 0/off/false disables)."""
    raw = (ctx.read_secret("ASF_CONTEXT_WINDOW", env_fallback=True) or "128000").strip().lower()
    if raw in {"0", "off", "false", "none"}:
        return None
    with contextlib.suppress(ValueError):
        val = int(raw)
        return val if val > 0 else None
    return 128000


def _configure_provider(ctx: sh.ProvisionContext, config: dict[str, Any], url: str, kind: str,
                        api_key: str) -> None:
    """Point OpenCode's "gateway" provider at AGENT_GATEWAY_URL; MLflow extras for type mlflow.

    The key is referenced as {env:AGENT_GATEWAY_API_KEY}, never written to disk.
    """
    provider = _block(_block(config, "provider"), PROVIDER)
    provider["npm"] = "@ai-sdk/openai-compatible"
    provider.setdefault("name", "Agent gateway")
    options = _block(provider, "options")
    options["baseURL"] = url
    options["apiKey"] = "{env:AGENT_GATEWAY_API_KEY}"
    models = _block(provider, "models")
    names = list(DEFAULT_MODELS)
    if kind == "mlflow":
        headers = _block(options, "headers")
        for key, value in MLFLOW_TRACE_HEADERS.items():
            headers.setdefault(key, value)
        names += [n for n in _mlflow_endpoints(ctx, url, api_key) if n not in names]
    ctx_window = _context_window(ctx)
    for name in names:
        model = models.get(name) if isinstance(models.get(name), dict) else {}
        model.setdefault("name", name)
        if ctx_window is not None:
            # OpenCode compacts the session automatically near this limit.
            model.setdefault("limit", {"context": ctx_window, "output": min(32000, ctx_window // 4)})
        models[name] = model
    disabled = config.get("disabled_providers") if isinstance(config.get("disabled_providers"), list) else []
    config["disabled_providers"] = disabled + [p for p in DISABLED_PROVIDERS if p not in disabled]


def _mlflow_endpoints(ctx: sh.ProvisionContext, url: str, api_key: str) -> list[str]:
    """Endpoint names an MLflow gateway serves; empty when it cannot be reached."""
    base = url[: -len(MLFLOW_API_SUFFIX)] if url.endswith(MLFLOW_API_SUFFIX) else url
    headers = {"User-Agent": "asf-opencode-provision/3"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        request = urllib.request.Request(f"{base}/ajax-api/3.0/mlflow/gateway/endpoints/list", headers=headers)
        with urllib.request.urlopen(request, timeout=2) as response:
            endpoints = json.loads(response.read().decode("utf-8")).get("endpoints", [])
    except Exception as exc:  # the configured sizes still work
        ctx.info(f"gateway endpoint discovery skipped: {exc.__class__.__name__}")
        return []
    return [e["name"] for e in endpoints if isinstance(e, dict) and e.get("name")]


# ---------------------------------------------------------------------------
# Model, prompts, MCP
# ---------------------------------------------------------------------------

def _apply_model(ctx: sh.ProvisionContext, config: dict[str, Any]) -> str:
    """Default OpenCode to the resolved gateway model (SCION_MODEL through model_aliases)."""
    model = sh.resolve_model(ctx) or f"{PROVIDER}/medium"
    provider, sep, model_id = model.partition("/")
    if not sep:
        provider, model_id = PROVIDER, model
    if provider != PROVIDER or not model_id:
        raise sh.ProvisionError(f"model {model!r}: this harness only serves agent gateway models")
    config["model"] = f"{PROVIDER}/{model_id}"
    # Titles and summaries run on the cheapest endpoint.
    config["small_model"] = f"{PROVIDER}/small"
    _block(_block(config, "provider"), PROVIDER).setdefault("models", {}).setdefault(model_id, {"name": model_id})
    return config["model"]


def _write_system_prompt(ctx: sh.ProvisionContext, config: dict[str, Any]) -> None:
    """Template prompt -> system_prompt_file, used as the primary agent's prompt; removed when unset."""
    relative = str(ctx.harness_config.get("system_prompt_file") or "")
    if not relative:
        return
    target = os.path.join(ctx.home, relative)
    agent = _block(_block(config, "agent"), PRIMARY_AGENT)
    prompt = ctx.read_input_text("system-prompt.md")
    if prompt.strip():
        os.makedirs(os.path.dirname(target), exist_ok=True)
        sh.atomic_write_text(target, prompt)
        agent["prompt"] = "{file:" + target + "}"
        return
    with contextlib.suppress(FileNotFoundError):
        os.remove(target)
    agent.pop("prompt", None)
    if not agent:
        config["agent"].pop(PRIMARY_AGENT)
    if not config["agent"]:
        config.pop("agent")


def _translate_mcp_server(name: str, spec: dict[str, Any]) -> dict[str, Any] | None:
    """Scion MCP server -> OpenCode's "mcp" entry (local/remote; same as upstream)."""
    transport = (spec.get("transport") or "").strip()
    if transport == "stdio" and isinstance(spec.get("command"), str) and spec["command"]:
        args = spec.get("args") if isinstance(spec.get("args"), list) else []
        out: dict[str, Any] = {"type": "local", "command": [spec["command"], *[str(a) for a in args]]}
        if isinstance(spec.get("env"), dict) and spec["env"]:
            out["environment"] = {str(k): str(v) for k, v in spec["env"].items()}
        return out
    if transport in ("sse", "streamable-http") and isinstance(spec.get("url"), str) and spec["url"]:
        out = {"type": "remote", "url": spec["url"]}
        if isinstance(spec.get("headers"), dict) and spec["headers"]:
            out["headers"] = {str(k): str(v) for k, v in spec["headers"].items()}
        return out
    print(f"asf-opencode provision: mcp server {name!r}: transport {transport!r} not usable, skipped",
          file=sys.stderr)
    return None


def _save_config(ctx: sh.ProvisionContext, config: dict[str, Any]) -> None:
    config.setdefault("$schema", CONFIG_SCHEMA)
    path = _config_path(ctx)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # MCP entries can carry env/header tokens, so the file is owner-only.
    old = os.umask(0o077)
    try:
        sh.atomic_write_json(path, config)
    finally:
        os.umask(old)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def provision(ctx: sh.ProvisionContext) -> None:
    resolved = ctx.select_auth(AUTH)
    api_key = ctx.read_secret("AGENT_GATEWAY_API_KEY", env_fallback=True).strip()
    url = _gateway_url(ctx, keyless=not api_key)
    kind = _gateway_type(ctx)

    config = _load_config(ctx)
    _configure_provider(ctx, config, url, kind, api_key)
    model = _apply_model(ctx, config)
    _write_system_prompt(ctx, config)
    config["autoupdate"] = False
    config["share"] = "disabled"
    permission = _block(config, "permission")
    for key, value in PERMISSIONS.items():
        permission.setdefault(key, value)

    def write_mcp(servers: dict[str, Any]) -> None:
        _block(config, "mcp").update(servers)

    sh.apply_mcp_translated(ctx, _translate_mcp_server, write_mcp)
    _save_config(ctx, config)

    # Role instructions go to AGENTS.md; OpenCode discovers skills itself.
    instructions = os.path.join(ctx.home, str(ctx.harness_config.get("instructions_file")
                                              or f"{DEFAULT_CONFIG_DIR}/AGENTS.md"))
    os.makedirs(os.path.dirname(instructions), exist_ok=True)
    sh.project_instructions(ctx, instructions, system_prompt_mode="none", include_skills=False)

    # A keyless local gateway still needs a value: the OpenAI-compatible client sends it as a bearer token.
    # SCION_USAGE_SOURCE=hooks enables sciontool's hook-sourced usage counters
    # from scion-bridge.js model-end events.
    ctx.write_outputs(resolved, env={"AGENT_GATEWAY_URL": url, "AGENT_GATEWAY_TYPE": kind,
                                     "AGENT_GATEWAY_API_KEY": api_key or "none",
                                     "SCION_USAGE_SOURCE": "hooks"})
    ctx.info(f"method={resolved.method} gateway={url} type={kind} model={model}")


if __name__ == "__main__":
    sh.run("asf-opencode", provision)
