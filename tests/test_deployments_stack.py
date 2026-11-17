"""core_healthy() with real-shaped `docker compose` output: one-shot jobs may exit 0, nothing else.

`docker` is replaced at the subprocess boundary; the argv decides which canned output is returned.
Row shapes follow `docker compose ps --all --format json` (Compose v2) and
`docker compose config --format json`.
"""
import json
import subprocess

import pytest

from deployments import base, stack

SERVICES = "dolt\nmlflow\ncreate-bucket\nscion\n"

CONFIG = {
    "name": "asf",
    "services": {
        "dolt": {"restart": "unless-stopped"},
        "mlflow": {"restart": "unless-stopped"},
        "create-bucket": {"restart": "no"},
        "scion": {"restart": "unless-stopped"},
    },
}


def ps_row(service, state, *, health="", exit_code=0):
    return {
        "Command": "\"docker-entrypoint.sh\"",
        "CreatedAt": "2026-11-04 23:40:00 +0100 CET",
        "ExitCode": exit_code,
        "Health": health,
        "Image": f"asf/{service}:latest",
        "Name": f"asf-{service}-1",
        "Project": "asf",
        "Publishers": [],
        "Service": service,
        "State": state,
        "Status": "Up 5 minutes" if state == "running" else "Exited (%d) 3 minutes ago" % exit_code,
    }


@pytest.fixture
def compose(monkeypatch):
    """Serve `ps` rows for the given state; `config` answers come from CONFIG."""
    state = {"rows": []}

    def fake_run(argv, **kwargs):
        if argv[:2] != ["docker", "compose"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "--services" in argv:
            return subprocess.CompletedProcess(argv, 0, SERVICES, "")
        if "config" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps(CONFIG), "")
        if "ps" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps(state["rows"]), "")
        raise AssertionError(f"unexpected compose call: {argv}")

    monkeypatch.setattr(base.subprocess, "run", fake_run)
    return state


def test_core_healthy_accepts_one_shot_exited_zero(compose):
    compose["rows"] = [
        ps_row("dolt", "running", health="healthy"),
        ps_row("mlflow", "running", health="healthy"),
        ps_row("create-bucket", "exited", exit_code=0),
        ps_row("scion", "running", health="healthy"),
    ]
    assert stack.core_healthy({})


def test_core_healthy_rejects_one_shot_that_failed(compose):
    compose["rows"] = [
        ps_row("dolt", "running", health="healthy"),
        ps_row("mlflow", "running", health="healthy"),
        ps_row("create-bucket", "exited", exit_code=1),
    ]
    assert not stack.core_healthy({})


def test_core_healthy_rejects_long_running_service_that_exited(compose):
    compose["rows"] = [
        ps_row("dolt", "exited", exit_code=0),
        ps_row("mlflow", "running", health="healthy"),
        ps_row("create-bucket", "exited", exit_code=0),
    ]
    assert not stack.core_healthy({})


def test_core_healthy_rejects_unhealthy_long_running_service(compose):
    compose["rows"] = [
        ps_row("dolt", "running", health="starting"),
        ps_row("mlflow", "running", health="healthy"),
        ps_row("create-bucket", "exited", exit_code=0),
    ]
    assert not stack.core_healthy({})


def test_core_healthy_rejects_one_shot_that_never_ran(compose):
    compose["rows"] = [
        ps_row("dolt", "running", health="healthy"),
        ps_row("mlflow", "running", health="healthy"),
    ]
    assert not stack.core_healthy({})


def test_one_shot_services_come_from_restart_policy(monkeypatch):
    raw = {"services": {"a": {"restart": "no"}, "b": {"restart": "unless-stopped"}, "c": {}}}
    monkeypatch.setattr(base.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, json.dumps(raw), ""))
    assert stack.one_shot_services(stack.core_stack({})) == {"a"}


@pytest.mark.parametrize("stderr,stdout", [
    ("container asf-otel-collector-1 is restarting", ""),
    ("", "failed to start services"),
])
def test_start_core_reports_compose_failure_output(compose, monkeypatch, stderr, stdout):
    monkeypatch.setattr(stack.ComposeStack, "up", lambda *a, **kw:
                        base.CommandResult(["docker", "compose", "up"], 1, stdout, stderr))
    with pytest.raises(base.DeployError) as error:
        stack.start_core({})
    assert (stderr or stdout) in error.value.message
    assert error.value.fix
