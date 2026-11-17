"""Log MLflow in to GitHub Copilot when it is the model provider (mlflow.provider: github_copilot).

LiteLLM in the MLflow container authenticates to Copilot with a GitHub OAuth access
token in the copilot-token volume. Three ways to put a valid one there:

- browser: GitHub device login; the step shows the code and detects the login
- token:   paste an existing Copilot-enabled GitHub OAuth token (hidden prompt)
- local:   reuse the Copilot login of an editor on this machine
           (~/.config/github-copilot/apps.json or hosts.json), when one is found

Every token is checked against Copilot first, then written to MLflow through stdin
(never argv or env) and MLflow restarts to pick it up. With --yes nothing is asked.

    python scripts/steps/copilot.py --dry-run
    python scripts/steps/copilot.py                 # make setup-copilot
    python scripts/steps/copilot.py --replace       # replace a stored login
    some-command | python scripts/steps/copilot.py --token-stdin
"""
import getpass
import json
import os
import sys
import webbrowser
from pathlib import Path

try:  # run as a script: put scripts/ on sys.path
    import _path  # noqa: F401
except ModuleNotFoundError:  # imported as steps.copilot
    pass

import doctor
import factory_env as fe
import requests
from steps import ui
from steps.common import Context, StepError, StepResult, cli

MLFLOW_CONTAINER = "mlflow-server"
TOKEN_DIR = "/var/lib/litellm-copilot"
TOKEN_PATH = f"{TOKEN_DIR}/access-token"
COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
DEVICE_URL = "https://github.com/login/device"
CODE_WAIT_SECONDS = 90
LOGIN_WAIT_SECONDS = 180
POLL_SECONDS = 3
RETRY = "make setup-copilot"


def has_token() -> bool:
    return fe.run(["docker", "exec", MLFLOW_CONTAINER, "test", "-s", TOKEN_PATH]).returncode == 0


def local_editor_token() -> str | None:
    """OAuth token of a Copilot editor login on this machine (VS Code, JetBrains, Neovim plugins)."""
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "github-copilot"
    for name in ("apps.json", "hosts.json"):
        try:
            data = json.loads((root / name).read_text())
        except (OSError, ValueError):
            continue
        for key, entry in data.items() if isinstance(data, dict) else ():
            if key.startswith("github.com") and isinstance(entry, dict) and entry.get("oauth_token"):
                return entry["oauth_token"]
    return None


def validate(token: str) -> bool:
    """True when GitHub issues a Copilot API key for this token. Never prints the token or response."""
    try:
        response = requests.get(COPILOT_TOKEN_URL, timeout=15, headers={
            "Authorization": f"token {token}", "Accept": "application/json",
            "Editor-Version": "vscode/1.85.1", "Editor-Plugin-Version": "copilot/1.155.0"})
    except requests.RequestException:
        raise StepError("Could not reach GitHub to check the Copilot token", ["check network access", RETRY]) from None
    if response.status_code in (401, 403, 404):
        return False
    if not response.ok:
        raise StepError(f"Copilot token check failed (HTTP {response.status_code})", [RETRY])
    return True


def install(token: str) -> None:
    """Write the token into MLflow's token volume through stdin, drop the cached API key, restart."""
    script = ("import os, sys; from pathlib import Path; "
              f"p = Path({TOKEN_PATH!r}); p.parent.mkdir(parents=True, exist_ok=True); "
              "fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); "
              "f = os.fdopen(fd, 'w'); f.write(sys.stdin.read().strip()); f.close(); "
              "p.with_name('api-key.json').unlink(missing_ok=True)")
    if fe.run(["docker", "exec", "-i", MLFLOW_CONTAINER, "python", "-c", script], input=token).returncode:
        raise StepError("Could not write the Copilot token into MLflow", [f"docker logs {MLFLOW_CONTAINER}", RETRY])
    _restart()


def _restart() -> None:
    with ui.activity("Restarting MLflow"):
        result = fe.run(["docker", "restart", MLFLOW_CONTAINER], timeout=120)
    if result.returncode:
        raise StepError("Could not restart MLflow", [f"docker logs {MLFLOW_CONTAINER}", RETRY])


def _clear_token() -> None:
    fe.run(["docker", "exec", MLFLOW_CONTAINER, "rm", "-f", TOKEN_PATH, f"{TOKEN_DIR}/api-key.json"])


def use_token(token: str, source: str) -> StepResult:
    token = token.strip()
    if not token:
        raise StepError("No Copilot token given", [RETRY])
    with ui.activity("Checking the token with GitHub Copilot"):
        ok = validate(token)
    if not ok:
        raise StepError(f"GitHub rejected the {source} for Copilot (expired, or no Copilot access)",
                        ["use a GitHub OAuth token from a Copilot login (gho_...), not a PAT", RETRY])
    install(token)
    return StepResult("done", f"MLflow logged in to GitHub Copilot ({source})")


def browser_login() -> StepResult:
    """Device login: restarted without a token, MLflow prints a fresh code to its log; wait for the token file."""
    _clear_token()
    since = fe.run(["docker", "exec", MLFLOW_CONTAINER, "date", "-u", "+%Y-%m-%dT%H:%M:%SZ"]).stdout.strip()
    _restart()
    found = {}

    def code_ready():  # only codes logged after the restart: older ones have expired
        found["code"] = doctor.copilot_device_code(since=since or None)
        return bool(found["code"])

    if not ui.wait_for("Waiting for MLflow's Copilot device code", code_ready, CODE_WAIT_SECONDS, POLL_SECONDS):
        raise StepError("MLflow did not print a Copilot device code",
                        [f"docker logs {MLFLOW_CONTAINER}", RETRY])
    ui.info(f"Open {DEVICE_URL}, enter the code {found['code']}, sign in with a Copilot-enabled")
    ui.info("GitHub account and authorize. Setup detects the login by itself.")
    try:
        webbrowser.open(DEVICE_URL, new=2)
    except webbrowser.Error:
        pass
    if not ui.wait_for("Waiting for browser authorization", has_token, LOGIN_WAIT_SECONDS, POLL_SECONDS):
        raise StepError("Copilot authorization was not detected in time", [RETRY])
    _restart()  # workers start with a fresh API key
    return StepResult("done", "MLflow logged in to GitHub Copilot (browser)")


def run(ctx: Context, **options) -> StepResult:
    if fe.gateway_type() != "mlflow" or fe.model_provider() != "github_copilot":
        return StepResult("skipped", f"model provider is {fe.model_provider()}, not github_copilot")
    if ctx.dry_run:
        ctx.log("would check MLflow's GitHub Copilot login and offer browser login or a pasted token")
        return StepResult("dry-run", "copilot: would check the login")
    ui.heading("GitHub Copilot login for MLflow")
    if fe.run(["docker", "exec", MLFLOW_CONTAINER, "true"]).returncode:
        raise StepError("MLflow is not running", ["make setup-services"])
    running = fe.run(["docker", "exec", MLFLOW_CONTAINER, "printenv", "MLFLOW_GATEWAY_PROVIDER"]).stdout.strip()
    if running != "github_copilot":
        raise StepError(f"the MLflow container still runs provider {running or 'unknown'}",
                        ["make setup-services (rewrites .env)", "docker compose up -d --force-recreate mlflow"])
    if options.get("token_stdin"):
        return use_token(sys.stdin.read(), "token from stdin")
    replace = options.get("replace")
    if has_token() and not replace:
        return StepResult("skipped", "MLflow is already logged in to GitHub Copilot (--replace to change)")
    if ctx.assume_yes:
        return StepResult("skipped", f"Copilot login needs you: run {RETRY}")
    local = local_editor_token()
    methods = {
        "browser": "GitHub device login in your browser (recommended)",
        "token": "Paste a Copilot-enabled GitHub OAuth token (hidden)",
        **({"local": "Reuse the Copilot login of an editor on this machine"} if local else {}),
        "later": f"Later: {RETRY}",
    }
    method = ui.choose("Copilot login method", methods)
    if method == "later":
        return StepResult("skipped", f"Copilot login deferred; run {RETRY}")
    if method == "local":
        return use_token(local, "editor Copilot login")
    if method == "token":
        ui.info("A GitHub OAuth token from a Copilot login (gho_...), not the agents' repository PAT.")
        try:
            token = getpass.getpass("    Copilot token (hidden; Enter = later): ")
        except EOFError:
            token = ""
        if not token.strip():
            return StepResult("skipped", f"Copilot login deferred; run {RETRY}")
        return use_token(token, "pasted token")
    return browser_login()


def add_arguments(parser):
    parser.add_argument("--replace", action="store_true", help="replace an existing Copilot login")
    parser.add_argument("--token-stdin", action="store_true",
                        help="read a Copilot-enabled GitHub OAuth token from stdin instead of asking")


main = cli(run, add_arguments, __doc__, needs_adapter=False)

if __name__ == "__main__":
    sys.exit(main())
