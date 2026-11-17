"""Shared paths, configuration and helpers for setup.py, doctor.py and friends.

Configuration: asf.yaml (committed defaults) deep-merged with asf.local.yaml
(gitignored, per machine). Secrets: .env.secrets. .env is generated from the
configuration for docker compose (see compose_env_values) and keeps any other
keys a user added.
"""
import copy
import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml
from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SCION_SRC = ROOT / "scion"
CONFIG_FILE = ROOT / "asf.yaml"
LOCAL_CONFIG_FILE = ROOT / "asf.local.yaml"
ENV_FILE = ROOT / ".env"
ENV_TEMPLATE = ROOT / ".env.template"
SECRETS_FILE = ROOT / ".env.secrets"
SECRETS_TEMPLATE = ROOT / ".env.secrets.template"
COMPOSE_FILE = ROOT / "compose.yaml"
COMPOSE_NETWORK = "ai-software-factory-network"

# One deployment: the Hub, its hosted Runtime Broker and the web UI run on this machine.
DEPLOYMENTS = {
    "local": "Scion runs on this machine: `scion server start` (Hub, broker, web UI).",
}
# Infrastructure services every deployment needs (Scion itself is separate).
CORE_SERVICES = ("dolt", "postgres", "storage", "mlflow")
DOLT_PASSWORD_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
# Placeholder AGENT_GATEWAY_API_KEY for a keyless local gateway: the opencode harness
# drops to a shell without a key, the local MLflow gateway ignores it.
GATEWAY_LOCAL_KEY = "local-gateway-no-auth"


# --- configuration (asf.yaml + asf.local.yaml) --------------------------------

def _merge(base, override):
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def _load(path):
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a YAML mapping")
    return data


def config():
    """Effective configuration: asf.yaml with asf.local.yaml on top."""
    return _merge(_load(CONFIG_FILE), _load(LOCAL_CONFIG_FILE))


def get(path, default=None, cfg=None):
    """Value at a dotted path, e.g. get("mlflow.port")."""
    node = cfg if cfg is not None else config()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def set_local(path, value):
    """Store one value in asf.local.yaml (created if missing), keeping the rest."""
    data = _load(LOCAL_CONFIG_FILE)
    node = data
    *parents, leaf = path.split(".")
    for part in parents:
        node = node.setdefault(part, {})
    node[leaf] = value
    header = "# Local overrides of asf.yaml (gitignored). Same structure as asf.yaml.\n"
    LOCAL_CONFIG_FILE.write_text(header + yaml.safe_dump(data, sort_keys=False))


def expand(path):
    return Path(os.path.expanduser(str(path)))


def hub_url(cfg=None):
    return get("scion.hub_url", "http://127.0.0.1:8080", cfg)


def broker_url(cfg=None):
    return get("scion.broker_url", "http://127.0.0.1:9800", cfg)


def install_path(cfg=None):
    return expand(get("scion.install_path", "~/.local/bin/scion", cfg))


def scion_home():
    """Host state directory; process SCION_HOME overrides .env, then ~/.scion."""
    value = os.environ.get("SCION_HOME") or dotenv_values(ENV_FILE).get("SCION_HOME")
    path = expand(value or Path.home() / ".scion")
    return path if path.is_absolute() else ROOT / path


def ensure_scion_home():
    """The pinned CLI uses HOME/.scion; support relocation through its symlink layout."""
    target = scion_home()
    default = Path.home() / ".scion"
    if target.resolve() == default.resolve():
        return
    if default.exists() or default.is_symlink():
        raise ValueError(f"{default} already exists and does not point to {target}. "
                         "Stop Scion and move the existing data to the configured SCION_HOME, "
                         "then replace the old directory with a symlink to it.")
    target.mkdir(parents=True, exist_ok=True)
    default.symlink_to(target, target_is_directory=True)


def mlflow_port(cfg=None):
    return int(get("mlflow.port", 5001, cfg))


def mlflow_url(cfg=None):
    """MLflow server URL as seen from this machine (health, API, doctor)."""
    return f"http://127.0.0.1:{mlflow_port(cfg)}"


def gateway_type(cfg=None):
    return str(get("agent_gateway.type", "mlflow", cfg) or "mlflow")


def gateway_url(cfg=None):
    """OpenAI-compatible base URL agents use (AGENT_GATEWAY_URL)."""
    url = get("agent_gateway.url", "", cfg)
    if url:
        return str(url).rstrip("/")
    if gateway_type(cfg) == "mlflow":
        return f"http://localhost:{mlflow_port(cfg)}/gateway/mlflow/v1"
    raise ValueError("agent_gateway.url must be set when agent_gateway.type is not mlflow")


def gateway_models(cfg=None):
    return list(get("agent_gateway.models", ["small", "medium", "large"], cfg))


def gateway_context_env(cfg=None):
    """Hub environment variables for agent_gateway.context_management."""
    cm = get("agent_gateway.context_management", {}, cfg) or {}
    enabled = bool(cm.get("enabled", True))
    return {"ASF_CONTEXT_WINDOW": str(cm.get("context_window", 128000) if enabled else 0)}


def dolt_url(cfg=None):
    return f"mysql://{get('dolt.user', 'beads', cfg)}@127.0.0.1:{get('dolt.port', 3307, cfg)}"


def deployment(cfg=None):
    return "local"


# Providers the MLflow AI Gateway can route to without an API key.
KEYLESS_PROVIDERS = ("ollama",)


def model_provider(cfg=None):
    """MLflow AI Gateway provider behind the endpoints (anthropic, openai, openai_compatible, ...)."""
    return str(get("mlflow.provider", "anthropic", cfg) or "anthropic")


def provider_needs_key(cfg=None):
    return gateway_type(cfg) == "mlflow" and model_provider(cfg) not in KEYLESS_PROVIDERS


def compose_env_values(cfg=None):
    """Keys .env must have for compose.yaml, derived from the configuration."""
    cfg = cfg if cfg is not None else config()
    port = mlflow_port(cfg)
    values = {
        "MLFLOW_PORT": str(port),
        "MLFLOW_SERVER_ALLOWED_HOSTS": f"localhost,localhost:{port},127.0.0.1,127.0.0.1:{port}",
        "MLFLOW_SERVER_CORS_ALLOWED_ORIGINS": f"http://localhost:{port},http://127.0.0.1:{port}",
        "DOLT_PORT": str(get("dolt.port", 3307, cfg)),
        "DOLT_DATABASE": str(get("dolt.database", "beads", cfg)),
        "DOLT_DB_USER": str(get("dolt.user", "beads", cfg)),
        "SCION_IMAGE_REGISTRY": str(get("scion.image_registry", "", cfg)),
        "MLFLOW_GATEWAY_PROVIDER": model_provider(cfg),
        "MLFLOW_GATEWAY_API_BASE": str(get("mlflow.api_base", "", cfg) or ""),
    }
    for name, models in (get("mlflow.endpoints", {}, cfg) or {}).items():
        values[f"MLFLOW_ENDPOINT_{name.upper().replace('-', '_')}"] = str(models)
    return values


def write_compose_env(cfg=None):
    """Create .env from .env.template if missing and set the configuration-derived keys."""
    if not ENV_FILE.exists():
        ENV_FILE.write_text(ENV_TEMPLATE.read_text() if ENV_TEMPLATE.exists() else "")
    current = dotenv_values(ENV_FILE)
    changed = []
    for key, value in compose_env_values(cfg).items():
        if current.get(key) != value:
            set_setting(ENV_FILE, key, value)
            changed.append(key)
    return changed


# --- secrets (.env.secrets) ---------------------------------------------------

def settings():
    """Secrets and compose values: .env, then .env.secrets, then matching process env."""
    values = {}
    for path in (ENV_FILE, SECRETS_FILE):
        if path.is_file():
            values.update({k: v for k, v in dotenv_values(path).items() if v is not None})
    values.update({k: v for k, v in os.environ.items()
                   if k in values or k.startswith(("SCION_", "AGENT_GATEWAY_", "DOLT_", "MODEL_PROVIDER_"))})
    return values


def is_placeholder(value):
    """True for unset values and template placeholders like `<email>` or `...`."""
    value = (value or "").strip()
    return not value or value.startswith("<") or value == "..."


def set_setting(path, key, value):
    path.touch(exist_ok=True)
    set_key(str(path), key, value, quote_mode="never")


# --- commands -----------------------------------------------------------------

def scion_commit():
    result = run(["git", "-C", str(SCION_SRC), "rev-parse", "HEAD"])
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def compose_env():
    """Environment overrides for Compose builds: pins the Scion image to the submodule commit."""
    return {"SCION_COMMIT": scion_commit()}


def which(name):
    return shutil.which(name)


def run(argv, timeout=60, env=None, input=None):
    """Run a command and capture output; never raises for a non-zero exit or a missing binary.

    Plain execution only: Scion calls are made through deployments.get_adapter().
    """
    try:
        return subprocess.run([str(a) for a in argv], capture_output=True, text=True,
                              timeout=timeout, env=env, input=input, cwd=ROOT)
    except FileNotFoundError as error:
        return subprocess.CompletedProcess(argv, 127, "", str(error))
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", f"timed out after {timeout}s")
