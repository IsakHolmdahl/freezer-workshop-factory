"""Publish Hub settings: admin role, shared env and secrets, gateway, git identity, harness-configs.

Ported from setup.py step_hub_wiring. Every command goes through the adapter, so
setup, doctor and the sync scripts share one way to reach the Hub. Re-running re-applies the
same values (no changes for the Hub), so this step is never reported as skipped.

    python scripts/steps/hub_wiring.py --dry-run
"""
import sys

try:  # run as a script: put scripts/ on sys.path
    import _path  # noqa: F401
except ModuleNotFoundError:  # imported as steps.hub_wiring
    pass

import factory_env as fe
from steps.common import Context, StepError, StepResult, cli

SCION_FLAGS = ["--global", "--yes", "--non-interactive"]  # arguments after the `scion` binary
LEGACY_HUB_ENV = ("MLFLOW_TRACKING_URI", "MLFLOW_API_KEY")  # names of earlier versions
DEFAULT_DOLT_PASSWORD = "0" * 48


def run(ctx: Context, **options) -> StepResult:
    if ctx.dry_run:
        ctx.log("would publish Hub env, secrets, gateway settings and harness-configs")
        return StepResult("dry-run", "hub wiring: would publish")
    adapter = ctx.adapter
    if not adapter.service_endpoints().hub_agent:
        raise StepError("Hub is not configured for this deployment",
                        ["./setup.py --doctor shows the Scion endpoints"])
    values = fe.settings()
    adapter.prepare_publish()
    _admin(adapter, values)
    _database(adapter, values)
    _gateway(adapter, values)
    _git_identity(adapter, values)
    _sync(adapter, values)
    return StepResult("done", "Hub settings published")


def _hub(adapter, *args, check=True):
    """Run `scion --global ... <args>` through the adapter; StepError on failure unless check=False."""
    result = adapter.scion([*SCION_FLAGS, *args])
    if check and not result.ok:
        raise StepError(f"scion {' '.join(args[:3])} failed: {_tail(result)}",
                        ["./setup.py --doctor shows whether Scion is running"])
    return result


def _tail(result) -> str:
    return (result.stderr or result.stdout or f"exit {result.returncode}").strip()


def _admin(adapter, values) -> None:
    email = values.get("SCION_ADMIN_EMAIL")
    if not fe.is_placeholder(email):
        adapter.scion(["admin", "promote", "--email", email])  # best effort: the result is not checked


def _database(adapter, values) -> None:
    password = values.get("DOLT_DB_PASSWORD") or DEFAULT_DOLT_PASSWORD
    _hub(adapter, "hub", "env", "set", "--scope", "hub", "--as-needed", "DOLT_URL", fe.dolt_url())
    prefix = str(fe.get("beads.prefix", "") or "")
    if prefix:
        _hub(adapter, "hub", "env", "set", "--scope", "hub", "--always", "BEADS_PREFIX", prefix)
    else:  # derived per project by the agent entrypoint
        _hub(adapter, "hub", "env", "clear", "--scope", "hub", "BEADS_PREFIX", check=False)
    _hub(adapter, "hub", "secret", "set", "--scope", "hub", "DOLT_DB_PASSWORD", password)


def _gateway(adapter, values) -> None:
    """Every agent gets the gateway URL, type and a key. Without a key the harness drops to a shell."""
    for key, value in (("AGENT_GATEWAY_URL", fe.gateway_url()), ("AGENT_GATEWAY_TYPE", fe.gateway_type()),
                       *fe.gateway_context_env().items()):
        _hub(adapter, "hub", "env", "set", "--scope", "hub", "--always", key, value)
    key = values.get("AGENT_GATEWAY_API_KEY")
    if fe.is_placeholder(key):
        key = fe.GATEWAY_LOCAL_KEY  # a keyless local gateway ignores the placeholder
    _hub(adapter, "hub", "env", "set", "--scope", "hub", "--always", "--secret",
         "AGENT_GATEWAY_API_KEY", key)
    for legacy in LEGACY_HUB_ENV:
        _hub(adapter, "hub", "env", "clear", "--scope", "hub", legacy, check=False)


def _git_identity(adapter, values) -> None:
    name = values.get("SCION_SERVER_AUTH_DISPLAYNAME") or _git_config("user.name")
    email = (values.get("SCION_SERVER_AUTH_EMAIL") or values.get("SCION_ADMIN_EMAIL")
             or _git_config("user.email"))
    if not fe.is_placeholder(name):
        _hub(adapter, "hub", "env", "set", "--scope", "hub", "--always", "ASF_GIT_AUTHOR_NAME", name)
    if not fe.is_placeholder(email):
        _hub(adapter, "hub", "env", "set", "--scope", "hub", "--always", "ASF_GIT_AUTHOR_EMAIL", email)


def _git_config(key: str) -> str:
    return fe.run(["git", "config", key]).stdout.strip()


def _sync(adapter, values) -> None:
    """Install harness-configs, skills and templates (the Hub must be running)."""
    result = adapter.run_repo_script("sync-factory.py", [
        "--skip-build", "--skip-binary", "--dolt-url", fe.dolt_url()])
    if not result.ok:
        raise StepError(f"sync-factory.py failed: {_tail(result)}",
                        ["re-run: scripts/sync-factory.py --skip-build --skip-binary"])


main = cli(run, None, __doc__)

if __name__ == "__main__":
    sys.exit(main())
