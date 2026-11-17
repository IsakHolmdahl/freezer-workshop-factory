"""Tests for the scion_settings_step, scion_start and hub_wiring steps with a fake adapter."""
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


from steps import hub_wiring, scion_settings_step, scion_start  # noqa: E402
from steps.common import StepError  # noqa: E402
from deployments.base import CommandResult
from test_steps_fakes import FakeAdapter, make_ctx  # noqa: E402,F401  (no_live_side_effects autouse)
import factory_env as fe  # noqa: E402


# --- scion_settings_step ---------------------------------------------------------

def test_settings_written_with_private_mode(tmp_path):
    adapter = FakeAdapter(tmp_path)
    ctx, _ = make_ctx(adapter)
    result = scion_settings_step.run(ctx)
    path = tmp_path / "settings.yaml"
    assert result.status == "done"
    assert yaml.safe_load(path.read_text()) == {"rendered": True}
    assert path.stat().st_mode & 0o777 == 0o600


def test_settings_second_run_is_skipped(tmp_path):
    adapter = FakeAdapter(tmp_path)
    ctx, _ = make_ctx(adapter)
    scion_settings_step.run(ctx)
    assert scion_settings_step.run(ctx).status == "skipped"


def test_settings_previous_version_is_kept_as_backup(tmp_path):
    (tmp_path / "settings.yaml").write_text("old: 1\n")
    adapter = FakeAdapter(tmp_path)
    ctx, lines = make_ctx(adapter)
    scion_settings_step.run(ctx)
    assert (tmp_path / "settings.yaml.bak").read_text() == "old: 1\n"
    assert any("previous version" in line for line in lines)


def test_settings_dry_run_writes_nothing(tmp_path):
    adapter = FakeAdapter(tmp_path)
    ctx, _ = make_ctx(adapter, dry_run=True)
    assert scion_settings_step.run(ctx).status == "dry-run"
    assert not (tmp_path / "settings.yaml").exists()


def test_settings_invalid_yaml_is_a_step_error_with_fix(tmp_path):
    (tmp_path / "settings.yaml").write_text("a: [unclosed\n")
    ctx, _ = make_ctx(FakeAdapter(tmp_path))
    with pytest.raises(StepError) as error:
        scion_settings_step.run(ctx)
    assert "not valid YAML" in error.value.message
    assert error.value.fix


# --- scion_start -------------------------------------------------------------------

def test_start_skipped_when_hub_already_healthy(monkeypatch):
    monkeypatch.setattr(scion_start, "hub_healthy", lambda url: True)
    adapter = FakeAdapter(running=True, healthy=True)
    ctx, _ = make_ctx(adapter)
    assert scion_start.run(ctx).status == "skipped"
    assert adapter.started == []


def test_start_starts_and_waits_for_hub(monkeypatch):
    adapter = FakeAdapter()
    # The Hub answers only once the adapter has started it.
    monkeypatch.setattr(scion_start, "hub_healthy", lambda url: adapter.running)
    monkeypatch.setattr(scion_start, "wait_for", lambda probe, timeout: probe())
    ctx, _ = make_ctx(adapter)
    assert scion_start.run(ctx).status == "done"
    assert adapter.started == [True]


def test_start_unhealthy_is_a_step_error_with_fix(monkeypatch):
    monkeypatch.setattr(scion_start, "hub_healthy", lambda url: False)
    monkeypatch.setattr(scion_start, "wait_for", lambda probe, timeout: False)
    ctx, _ = make_ctx(FakeAdapter(start_healthy=False))
    with pytest.raises(StepError) as error:
        scion_start.run(ctx)
    assert "did not become healthy" in error.value.message
    assert error.value.fix


def test_start_dry_run_does_not_start(monkeypatch):
    monkeypatch.setattr(scion_start, "hub_healthy", lambda url: False)
    adapter = FakeAdapter()
    ctx, lines = make_ctx(adapter, dry_run=True)
    assert scion_start.run(ctx).status == "dry-run"
    assert adapter.started == []
    assert lines


def test_start_help_exits_zero():
    with pytest.raises(SystemExit) as exit_info:
        scion_start.main(["--help"])
    assert exit_info.value.code == 0


# --- hub_wiring ----------------------------------------------------------------------

@pytest.fixture
def settings(monkeypatch):
    values = {"SCION_ADMIN_EMAIL": "admin@example.com", "COPILOT_GITHUB_TOKEN": "tok-1"}
    monkeypatch.setattr(fe, "settings", lambda: values)
    monkeypatch.setattr(fe, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "git-value\n", ""))
    monkeypatch.setattr(fe, "dolt_url", lambda cfg=None: "mysql://beads@127.0.0.1:3307")
    monkeypatch.setattr(fe, "gateway_url", lambda cfg=None: "http://gw")
    monkeypatch.setattr(fe, "gateway_type", lambda cfg=None: "openai")
    monkeypatch.setattr(fe, "gateway_context_env", lambda cfg=None: {})
    return values


def test_hub_wiring_publishes_through_adapter(settings):
    adapter = FakeAdapter()
    ctx, _ = make_ctx(adapter)
    assert hub_wiring.run(ctx).status == "done"
    assert adapter.published
    joined = [" ".join(c) for c in adapter.commands]
    assert any("hub secret set --scope hub DOLT_DB_PASSWORD" in c for c in joined)
    assert any("AGENT_GATEWAY_API_KEY" in c for c in joined)
    assert any("admin promote --email admin@example.com" in c for c in joined)
    assert not any("COPILOT" in c for c in joined)
    assert adapter.repo_scripts[0][0] == "sync-factory.py"


def test_hub_wiring_secrets_never_go_through_argv_other_than_scion(settings):
    adapter = FakeAdapter()
    ctx, _ = make_ctx(adapter)
    hub_wiring.run(ctx)
    assert all(argv[0] != "scion" or "--global" in argv for argv in adapter.commands)


def test_hub_wiring_failed_command_is_a_step_error_with_fix(settings):
    adapter = FakeAdapter(commands_fail=["DOLT_URL"])
    ctx, _ = make_ctx(adapter)
    with pytest.raises(StepError) as error:
        hub_wiring.run(ctx)
    assert "failed" in error.value.message
    assert error.value.fix


def test_hub_wiring_sync_error_preserves_cause_after_progress_output(settings, monkeypatch):
    adapter = FakeAdapter()
    detail = "Using hub: http://localhost:8080\n" * 10 + "AttributeError: template import failed"
    monkeypatch.setattr(adapter, "run_repo_script", lambda *a:
                        CommandResult(["sync-factory.py"], 1, stderr=detail))
    with pytest.raises(StepError) as error:
        hub_wiring.run(make_ctx(adapter)[0])
    assert detail in error.value.message


def test_hub_wiring_dry_run_runs_no_command(settings):
    adapter = FakeAdapter()
    ctx, _ = make_ctx(adapter, dry_run=True)
    assert hub_wiring.run(ctx).status == "dry-run"
    assert adapter.commands == [] and adapter.repo_scripts == [] and not adapter.published


def test_hub_wiring_does_not_publish_local_github_credentials(settings):
    settings["GITHUB_TOKEN"] = "github_pat_test"
    adapter = FakeAdapter()
    hub_wiring.run(make_ctx(adapter)[0])
    assert not any("GITHUB_TOKEN" in command for command in adapter.commands)


def test_hub_wiring_publishes_gateway_url_type_and_key_to_every_agent(settings):
    settings["AGENT_GATEWAY_API_KEY"] = "gw-key-1"
    adapter = FakeAdapter()
    ctx, _ = make_ctx(adapter)
    hub_wiring.run(ctx)
    joined = [" ".join(c) for c in adapter.commands]
    assert any("hub env set --scope hub --always AGENT_GATEWAY_URL http://gw" in c for c in joined)
    assert any("hub env set --scope hub --always AGENT_GATEWAY_TYPE openai" in c for c in joined)
    key_call = next(c for c in adapter.commands if "AGENT_GATEWAY_API_KEY" in c)
    assert key_call[-1] == "gw-key-1" and "--secret" in key_call


def test_hub_wiring_uses_local_key_for_keyless_gateway(settings):
    settings["AGENT_GATEWAY_API_KEY"] = "<key>"  # template placeholder
    adapter = FakeAdapter()
    ctx, _ = make_ctx(adapter)
    hub_wiring.run(ctx)
    key_call = next(c for c in adapter.commands if "AGENT_GATEWAY_API_KEY" in c)
    assert key_call[-1] == fe.GATEWAY_LOCAL_KEY
    assert any(c[-3:] == ["clear", "--scope", "hub"] or "MLFLOW_API_KEY" in c and "clear" in c
               for c in adapter.commands)
