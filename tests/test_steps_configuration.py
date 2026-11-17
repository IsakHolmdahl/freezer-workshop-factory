"""Tests for the configuration step. Config, .env and .env.secrets live in tmp_path.

The git and compose probes are faked; the autouse guard fails any real process.
"""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

from steps import configuration, ui  # noqa: E402
from steps.common import Context  # noqa: E402

DOLT_PASSWORD = "a" * 40


def fake_stack(volume):
    """Stand-in for deployments.stack.core_stack whose volume() answers `volume`."""
    return lambda cfg, **kw: SimpleNamespace(volume=lambda name: volume)


@pytest.fixture(autouse=True)
def no_live_side_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError(f"test attempted a subprocess: {args[0] if args else kwargs}")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(os, "system", forbidden)


@pytest.fixture
def files(monkeypatch, tmp_path):
    """Point every config file at tmp_path and make git identity deterministic."""
    local = tmp_path / "asf.local.yaml"
    env = tmp_path / ".env"
    secrets_file = tmp_path / ".env.secrets"
    template = tmp_path / ".env.secrets.template"
    template.write_text("SCION_ADMIN_EMAIL=<email>\nSCION_SERVER_AUTH_DISPLAYNAME=<name>\nGITHUB_TOKEN=\n"
                        "DOLT_DB_PASSWORD=<pw>\nDOLT_ROOT_PASSWORD=<pw>\n")
    monkeypatch.setattr(configuration.fe, "LOCAL_CONFIG_FILE", local)
    monkeypatch.setattr(configuration.fe, "ENV_FILE", env)
    monkeypatch.setattr(configuration.fe, "SECRETS_FILE", secrets_file)
    monkeypatch.setattr(configuration.fe, "SECRETS_TEMPLATE", template)
    monkeypatch.setattr(configuration.fe, "ENV_TEMPLATE", tmp_path / "missing.template")
    monkeypatch.setattr(configuration, "core_stack", fake_stack(None))
    monkeypatch.setattr(configuration.fe, "run",
                        lambda argv, **kw: SimpleNamespace(stdout={"user.email": "dev@example.com",
                                                                   "user.name": "Dev Eloper"}[argv[-1]]))
    monkeypatch.setattr(configuration.fe, "gateway_url", lambda cfg=None: "http://localhost:5001/gateway/mlflow/v1")
    monkeypatch.setattr(configuration.fe, "gateway_type", lambda cfg=None: "mlflow")
    monkeypatch.setattr(ui, "ASSUME_YES", True)
    return SimpleNamespace(local=local, env=env, secrets=secrets_file)


def ctx(dry_run=False):
    lines = []
    return Context(adapter=None, cfg={}, dry_run=dry_run, assume_yes=True, log=lines.append), lines


def read(path):
    from dotenv import dotenv_values
    return dict(dotenv_values(path)) if path.exists() else {}


def test_first_run_writes_secrets_identity_and_provider_env(files):
    result = configuration.run(ctx()[0])
    assert result.status == "done"
    secrets_values = read(files.secrets)
    assert secrets_values["SCION_ADMIN_EMAIL"] == "dev@example.com"
    assert secrets_values["SCION_SERVER_AUTH_DISPLAYNAME"] == "Dev Eloper"
    assert len(secrets_values["DOLT_DB_PASSWORD"]) == 48
    assert read(files.env)["MLFLOW_GATEWAY_PROVIDER"] == "anthropic"
    assert not files.local.exists()  # --yes never chooses a provider or asks for a key


def test_secrets_file_is_private(files):
    configuration.run(ctx()[0])
    assert oct(files.secrets.stat().st_mode & 0o777) == "0o600"


def test_second_run_is_skipped_and_keeps_generated_passwords(files):
    configuration.run(ctx()[0])
    first = read(files.secrets)["DOLT_DB_PASSWORD"]
    result = configuration.run(ctx()[0])
    assert result.status == "skipped"
    assert read(files.secrets)["DOLT_DB_PASSWORD"] == first


def test_existing_dolt_volume_gets_the_zero_default(files, monkeypatch):
    monkeypatch.setattr(configuration, "core_stack", fake_stack("asf_dolt-data"))
    configuration.run(ctx()[0])
    assert read(files.secrets)["DOLT_DB_PASSWORD"] == "0" * 48


def test_dry_run_writes_nothing(files):
    result = configuration.run(ctx(dry_run=True)[0])
    assert result.status == "dry-run"
    assert not files.local.exists() and not files.secrets.exists() and not files.env.exists()


def test_bad_gateway_config_is_a_step_error_with_fix(files, monkeypatch):
    from steps.common import StepError

    def broken(cfg=None):
        raise ValueError("agent_gateway.url must be set when agent_gateway.type is not mlflow")

    monkeypatch.setattr(configuration.fe, "gateway_url", broken)
    with pytest.raises(StepError) as error:
        configuration.run(ctx()[0])
    assert "agent_gateway.url" in error.value.message
    assert error.value.fix


def test_invalid_admin_email_is_asked_again_in_interactive_mode(files, monkeypatch):
    monkeypatch.setattr(ui, "ASSUME_YES", False)
    answers = iter(["not-an-email", "ops@example.com", "Ops Person"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    configuration.run(ctx()[0])
    assert read(files.secrets)["SCION_ADMIN_EMAIL"] == "ops@example.com"


def test_placeholder_secret_values_are_cleared(files):
    files.secrets.write_text("OTHER_TOKEN=<placeholder>\nGITHUB_TOKEN=\n")
    configuration.run(ctx()[0])
    assert read(files.secrets).get("SCION_ADMIN_EMAIL") == "dev@example.com"


def test_cli_help_exits_zero():
    with pytest.raises(SystemExit) as exit_info:
        configuration.main(["--help"])
    assert exit_info.value.code == 0


def test_interactive_run_asks_for_provider_and_key(files, monkeypatch):
    files.secrets.write_text("SCION_ADMIN_EMAIL=dev@example.com\nSCION_SERVER_AUTH_DISPLAYNAME=Dev\n")
    monkeypatch.setattr(ui, "choose", lambda prompt, choices: "openai")
    monkeypatch.setattr(configuration.getpass, "getpass", lambda prompt="": "sk-test")
    interactive = Context(adapter=None, cfg={}, dry_run=False, assume_yes=False, log=lambda _: None)
    configuration.run(interactive)
    assert read(files.secrets)["MODEL_PROVIDER_API_KEY"] == "sk-test"
    assert "provider: openai" in files.local.read_text()
    assert read(files.env)["MLFLOW_GATEWAY_PROVIDER"] == "openai"


def test_keyless_provider_asks_for_base_url_and_no_key(files, monkeypatch):
    files.secrets.write_text("SCION_ADMIN_EMAIL=dev@example.com\nSCION_SERVER_AUTH_DISPLAYNAME=Dev\n")
    monkeypatch.setattr(ui, "choose", lambda prompt, choices: "ollama")
    monkeypatch.setattr(ui, "ask", lambda question, default="": default)
    monkeypatch.setattr(configuration.getpass, "getpass", lambda prompt="": pytest.fail("ollama needs no key"))
    interactive = Context(adapter=None, cfg={}, dry_run=False, assume_yes=False, log=lambda _: None)
    configuration.run(interactive)
    assert read(files.env)["MLFLOW_GATEWAY_API_BASE"] == "http://host.docker.internal:11434"
    assert "MODEL_PROVIDER_API_KEY" not in read(files.secrets)


def test_existing_key_is_never_asked_again(files, monkeypatch):
    files.secrets.write_text("MODEL_PROVIDER_API_KEY=sk-have\nSCION_ADMIN_EMAIL=dev@example.com\n"
                             "SCION_SERVER_AUTH_DISPLAYNAME=Dev\n")
    monkeypatch.setattr(ui, "choose", lambda prompt, choices: pytest.fail("must not ask"))
    interactive = Context(adapter=None, cfg={}, dry_run=False, assume_yes=False, log=lambda _: None)
    configuration.run(interactive)
    assert read(files.secrets)["MODEL_PROVIDER_API_KEY"] == "sk-have"


def test_cli_dry_run_exit_code_is_zero(files, capsys):
    assert configuration.main(["--dry-run"]) == 0
    assert not files.secrets.exists()


def test_local_config_overrides_flow_into_env(files, monkeypatch):
    files.local.write_text("mlflow:\n  port: 5099\n  endpoints:\n    large: x:0.5,y:0.5\n")
    files.env.write_text("MLFLOW_PORT=5001\nMY_OWN=keep\n")
    monkeypatch.setattr(configuration.fe, "gateway_url", fe_gateway_url)
    configuration.run(ctx()[0])
    env = read(files.env)
    assert env["MLFLOW_PORT"] == "5099" and env["MY_OWN"] == "keep"
    assert env["MLFLOW_ENDPOINT_LARGE"] == "x:0.5,y:0.5"
    assert env["MLFLOW_ENDPOINT_SMALL"] == "claude-haiku-5-5"  # from asf.yaml


def fe_gateway_url(cfg=None):
    return "http://localhost:5099/gateway/mlflow/v1"
