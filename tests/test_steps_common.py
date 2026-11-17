"""Tests for the setup step framework and the services step, with a fake adapter.

No test touches Docker, Scion or the network: the guard fixture below fails any
test that runs a process outside the allowlist.
"""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

from steps import common, services  # noqa: E402
from steps.common import Context, StepError  # noqa: E402

@pytest.fixture(autouse=True)
def no_live_side_effects(monkeypatch):
    """Fail on any subprocess that is not a read-only allowlisted command."""
    def forbidden(*args, **kwargs):
        raise AssertionError(f"test attempted a subprocess: {args[0] if args else kwargs}")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(os, "execv", forbidden)


def make_ctx(adapter, dry_run=False):
    lines = []
    return Context(adapter=adapter, cfg={}, dry_run=dry_run, log=lines.append), lines


def core_fakes(monkeypatch, *, healthy_before=False, healthy_after=True):
    """Replace the core-stack boundary of the services step; records start_core calls."""
    state = {"healthy": healthy_before, "started": []}

    def fake_start(cfg, *, build=True, dry_run=False, log=print, **kwargs):
        state["started"].append((build, dry_run))
        state["healthy"] = healthy_after

    monkeypatch.setattr(services, "start_core", fake_start)
    monkeypatch.setattr(services, "core_healthy", lambda cfg, **kw: state["healthy"])
    return state


def test_services_starts_core_stack_when_not_healthy(monkeypatch):
    state = core_fakes(monkeypatch)
    ctx, _ = make_ctx(None)
    assert services.run(ctx).status == "done"
    assert state["started"] == [(True, False)]


def test_services_is_idempotent_when_core_healthy(monkeypatch):
    state = core_fakes(monkeypatch, healthy_before=True)
    ctx, _ = make_ctx(None)
    assert services.run(ctx).status == "skipped"
    assert state["started"] == []


def test_services_force_starts_even_when_healthy(monkeypatch):
    state = core_fakes(monkeypatch, healthy_before=True)
    ctx, _ = make_ctx(None)
    assert services.run(ctx, force=True).status == "done"
    assert state["started"] == [(True, False)]


def test_services_dry_run_does_not_start(monkeypatch):
    state = core_fakes(monkeypatch)
    ctx, _ = make_ctx(None, dry_run=True)
    assert services.run(ctx).status == "dry-run"
    assert state["started"] == [(True, True)]


def test_services_unhealthy_after_start_is_a_step_error(monkeypatch):
    core_fakes(monkeypatch, healthy_after=False)
    ctx, _ = make_ctx(None)
    with pytest.raises(StepError) as error:
        services.run(ctx)
    assert "did not become healthy" in error.value.message
    assert error.value.fix


def test_no_build_flag_is_passed_through(monkeypatch):
    state = core_fakes(monkeypatch)
    ctx, _ = make_ctx(None)
    services.run(ctx, build=False)
    assert state["started"] == [(False, False)]


def test_services_starts_core_stack_without_scion_service(monkeypatch):
    """Regression (review asf-vqq.13 #1): the services step must start MLflow/Dolt/telemetry."""
    from deployments import stack

    monkeypatch.setattr(stack.fe, "compose_env", lambda: {"SCION_COMMIT": "abc123"})
    calls = []
    monkeypatch.setattr(stack, "run_command", lambda argv, **kw: calls.append(list(argv)) or SimpleNamespace(
        ok=True, stdout="", returncode=0))
    monkeypatch.setattr(stack.ComposeStack, "services", lambda self: ["dolt", "scion", "mlflow"])
    monkeypatch.setattr(stack.ComposeStack, "up",
                        lambda self, services=(), **kw: calls.append(["up", *services]) or SimpleNamespace(ok=True))
    stack.start_core({}, build=True)
    assert ["up", "dolt", "mlflow"] in calls


def test_start_core_creates_missing_external_network(monkeypatch):
    from deployments import stack

    monkeypatch.setattr(stack.fe, "compose_env", lambda: {})
    calls = []

    def fake_command(argv, **kwargs):
        calls.append(list(argv))
        return SimpleNamespace(ok=argv[1:3] != ["network", "inspect"], stdout="", returncode=0)

    monkeypatch.setattr(stack, "run_command", fake_command)
    monkeypatch.setattr(stack.ComposeStack, "services", lambda self: ["dolt"])
    monkeypatch.setattr(stack.ComposeStack, "up", lambda self, services=(), **kw: SimpleNamespace(ok=True))
    stack.start_core({}, build=True)
    assert ["docker", "network", "create", stack.fe.COMPOSE_NETWORK] in calls


def test_step_error_renders_message_and_fix(capsys):
    def boom(ctx, **options):
        raise StepError("Docker is not running", fix=["sudo systemctl start docker"])

    main = common.cli(boom, None, "doc")
    original = common.Context.from_args
    common.Context.from_args = classmethod(lambda cls, args, **kw: make_ctx(None)[0])
    try:
        assert main([]) == 1
    finally:
        common.Context.from_args = original
    err = capsys.readouterr().err
    assert "✘ Docker is not running" in err
    assert "try: sudo systemctl start docker" in err


def test_deploy_error_renders_like_step_error(monkeypatch, capsys):
    """QA nit (asf-vqq.14 #2): a standalone step with no deployment shows a clean error, not a traceback."""
    from deployments.base import DeployError

    def boom(ctx, **options):
        raise DeployError("scion.deployment is not set", fix=["./setup.py --deployment local|compose|containers"])

    main = common.cli(boom, None, "doc", needs_adapter=False)
    monkeypatch.setattr(common.Context, "from_args", classmethod(lambda cls, args, **kw: make_ctx(None)[0]))
    assert main([]) == 1
    err = capsys.readouterr().err
    assert "✘ scion.deployment is not set" in err
    assert "try: ./setup.py --deployment local|compose|containers" in err
    assert "Traceback" not in err


def test_cli_help_exits_zero_for_services():
    with pytest.raises(SystemExit) as exit_info:
        services.main(["--help"])
    assert exit_info.value.code == 0


def test_needs_adapter_false_gives_context_without_adapter(monkeypatch):
    import argparse
    import factory_env as fe

    monkeypatch.setattr(fe, "config", lambda: {"x": 1})
    args = argparse.Namespace(dry_run=True, yes=True, deployment=None)
    ctx = common.Context.from_args(args, needs_adapter=False)
    assert ctx.adapter is None and ctx.cfg == {"x": 1} and ctx.dry_run is True


def test_steps_declared_without_adapter_run_standalone(capsys):
    from steps import build, configuration, prerequisites, scion_cli, secrets

    for module in (prerequisites, secrets, scion_cli, configuration, build):
        with pytest.raises(SystemExit):
            module.main(["--help"])
        capsys.readouterr()
