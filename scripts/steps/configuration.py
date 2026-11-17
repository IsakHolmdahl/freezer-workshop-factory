"""Write the configuration: .env for docker compose, secrets, the model provider key and the admin identity.

Needs no running service: it reads and writes asf.local.yaml, .env and .env.secrets only. Re-running changes nothing that is set.

    python scripts/steps/configuration.py --dry-run
    python scripts/steps/configuration.py --yes
"""
import getpass
import re
import secrets as token_source
import sys

try:  # run as a script: put scripts/ on sys.path
    import _path  # noqa: F401
except ModuleNotFoundError:  # imported as steps.configuration: scripts/ is already on sys.path
    pass

from dotenv import dotenv_values

import factory_env as fe
from deployments.stack import core_stack
from steps import ui
from steps.common import Context, StepError, StepResult, cli

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def run(ctx: Context, **options) -> StepResult:
    """Configuration needs no adapter: `ctx.adapter` is None."""
    if ctx.dry_run:
        ctx.log("would write asf.local.yaml, .env and .env.secrets (missing values only)")
        return StepResult("dry-run", "configuration: would write missing values")
    ui.heading("Configuration (asf.yaml, asf.local.yaml, .env.secrets)")
    if not fe.SECRETS_FILE.exists():
        fe.SECRETS_FILE.write_text(fe.SECRETS_TEMPLATE.read_text())
        fe.SECRETS_FILE.chmod(0o600)
        ui.info(f"created {fe.SECRETS_FILE.name} from {fe.SECRETS_TEMPLATE.name}")
    changed_before = _snapshot()

    try:
        fe.ensure_scion_home()
    except ValueError as error:
        raise StepError(str(error), ["update SCION_HOME in .env or migrate the existing Scion directory"]) from None
    try:
        ui.info(f"agent gateway: {fe.gateway_url()} (type {fe.gateway_type()})")
    except ValueError as error:
        raise StepError(f"asf.yaml: {error}", ["fix agent_gateway in asf.yaml or asf.local.yaml"]) from None
    _ensure_model_provider(ctx)
    changed = fe.write_compose_env()
    if changed:
        ui.info(f"updated .env for docker compose: {', '.join(changed)}")
    _ensure_dolt_passwords()
    _ensure_admin_identity()

    _clear_placeholders()

    if _snapshot() == changed_before:
        return StepResult("skipped", "configuration already complete")
    return StepResult("done", "configuration written")


def _snapshot() -> dict:
    return {path.name: path.read_text() if path.is_file() else None
            for path in (fe.LOCAL_CONFIG_FILE, fe.ENV_FILE, fe.SECRETS_FILE)}


# Providers offered in the prompt; any MLflow AI Gateway provider name works in asf.local.yaml.
PROVIDERS = {
    "anthropic": "Anthropic API (Claude models)",
    "openai": "OpenAI API",
    "gemini": "Google Gemini API",
    "openrouter": "OpenRouter",
    "openai_compatible": "Any OpenAI-compatible endpoint (asks for its base URL)",
    "ollama": "Ollama on this machine (no key)",
    "github_copilot": "GitHub Copilot subscription (GitHub login instead of a key: make setup-copilot)",
}
# Copilot model IDs (dotted versions) for the endpoints, set when Copilot is picked
# and the endpoints still name the asf.yaml defaults. Same defaults as ai-software-factory.
COPILOT_ENDPOINTS = {"small": "claude-haiku-5.5", "medium": "claude-sonnet-5.5", "large": "claude-sonnet-5.5"}
# Base URL asked for these providers; the default reaches the host from the MLflow container.
API_BASE_DEFAULTS = {"openai_compatible": "", "ollama": "http://host.docker.internal:11434"}


def _ensure_model_provider(ctx: Context) -> None:
    """MLflow routes the small/medium/large endpoints to one provider: make sure it has a key."""
    if fe.gateway_type() != "mlflow":
        return
    values = fe.settings()
    have_key = not fe.is_placeholder(values.get("MODEL_PROVIDER_API_KEY"))
    if have_key or not fe.provider_needs_key():
        ui.info(f"model provider: {fe.model_provider()} (endpoints: "
                + ", ".join(f"{k}={v}" for k, v in (fe.get("mlflow.endpoints", {}) or {}).items()) + ")")
        return
    if ctx.assume_yes:
        ui.info("MODEL_PROVIDER_API_KEY is not set: the model endpoints will not answer until you add it "
                "to .env.secrets and run make setup")
        return
    ui.info("Which model provider should MLflow route the small/medium/large endpoints to?")
    current = fe.model_provider()
    choices = {current: f"{PROVIDERS.get(current, current)} (current)",
               **{key: text for key, text in PROVIDERS.items() if key != current}}
    provider = ui.choose("Model provider", choices)
    if provider != current:
        fe.set_local("mlflow.provider", provider)
        ui.info(f"saved mlflow.provider: {provider} in {fe.LOCAL_CONFIG_FILE.name}; "
                "set the model names for it under mlflow.endpoints there")
    if provider in API_BASE_DEFAULTS and not fe.get("mlflow.api_base"):
        base = ui.ask(f"Base URL of the {provider} API, as seen from the MLflow container",
                      API_BASE_DEFAULTS[provider])
        if base:
            fe.set_local("mlflow.api_base", base.rstrip("/"))
    if provider == "github_copilot":
        if provider != current and fe.get("mlflow.endpoints", {}, fe.config()) == fe.get("mlflow.endpoints", {}, fe._load(fe.CONFIG_FILE)):
            for size, model in COPILOT_ENDPOINTS.items():
                fe.set_local(f"mlflow.endpoints.{size}", model)
            ui.info("saved Copilot model names under mlflow.endpoints in " + fe.LOCAL_CONFIG_FILE.name)
        ui.info("GitHub Copilot needs no API key; the copilot step (make setup-copilot) logs MLflow in")
        return
    if provider in fe.KEYLESS_PROVIDERS:
        return
    try:
        key = getpass.getpass(f"    API key for {provider} (hidden; Enter = add it later): ").strip()
    except EOFError:
        key = ""
    if key:
        fe.set_setting(fe.SECRETS_FILE, "MODEL_PROVIDER_API_KEY", key)
        ui.info("saved MODEL_PROVIDER_API_KEY in .env.secrets")
    else:
        ui.info("No key entered: add MODEL_PROVIDER_API_KEY to .env.secrets and run make setup again")


def _ensure_dolt_passwords() -> None:
    values = fe.settings()
    # An existing Dolt volume keeps the password it was created with: the 48-zero default.
    dolt_volume_exists = core_stack(fe.config()).volume("dolt-data") is not None
    for key in ("DOLT_DB_PASSWORD", "DOLT_ROOT_PASSWORD"):
        if fe.DOLT_PASSWORD_RE.match(values.get(key, "")):
            continue
        if dolt_volume_exists:
            fe.set_setting(fe.SECRETS_FILE, key, "0" * 48)
            ui.info(f"{key} was not set; wrote the 48-zero default to match the existing Dolt volume")
            continue
        fe.set_setting(fe.SECRETS_FILE, key, token_source.token_hex(24))
        ui.info(f"generated {key}")


def _ensure_admin_identity() -> None:
    values = fe.settings()
    git_email = fe.run(["git", "config", "user.email"]).stdout.strip()
    git_name = fe.run(["git", "config", "user.name"]).stdout.strip()
    email = values.get("SCION_ADMIN_EMAIL")
    if fe.is_placeholder(email):
        email = ui.ask("Your e-mail (becomes the Scion admin)", git_email)
        while not EMAIL_RE.match(email or ""):
            email = ui.ask("Please enter a valid e-mail address")
        fe.set_setting(fe.SECRETS_FILE, "SCION_ADMIN_EMAIL", email)
        fe.set_setting(fe.SECRETS_FILE, "SCION_SERVER_AUTH_EMAIL", email)
    if fe.is_placeholder(values.get("SCION_SERVER_AUTH_DISPLAYNAME")):
        fe.set_setting(fe.SECRETS_FILE, "SCION_SERVER_AUTH_DISPLAYNAME",
                       ui.ask("Your full name", git_name or getpass.getuser()))


def _clear_placeholders() -> None:
    """Template placeholders such as `<token>` would otherwise reach the agents as real values."""
    secret_keys = set(dotenv_values(fe.SECRETS_FILE)) if fe.SECRETS_FILE.exists() else set()
    for key, value in fe.settings().items():
        if fe.is_placeholder(value) and value and key in secret_keys:
            fe.set_setting(fe.SECRETS_FILE, key, "")
            ui.info(f"cleared placeholder {key} (optional, set it later in .env.secrets)")


main = cli(run, None, __doc__, needs_adapter=False)

if __name__ == "__main__":
    sys.exit(main())
