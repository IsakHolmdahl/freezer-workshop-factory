"""Tests for the copilot step (MLflow GitHub Copilot login). Docker and GitHub are faked."""
import io
import json
import subprocess
from types import SimpleNamespace

import pytest

from steps import copilot, ui
from steps.common import StepError
from test_steps_fakes import make_ctx  # noqa: F401  (no_live_side_effects autouse)


@pytest.fixture(autouse=True)
def copilot_provider(monkeypatch):
    monkeypatch.setattr(ui, "ASSUME_YES", False)
    monkeypatch.setattr(copilot.fe, "gateway_type", lambda cfg=None: "mlflow")
    monkeypatch.setattr(copilot.fe, "model_provider", lambda cfg=None: "github_copilot")


class FakeDocker:
    """Records docker calls; the token file exists once something was written to it."""

    def __init__(self, token=False, provider="github_copilot"):
        self.token, self.provider, self.calls, self.written = token, provider, [], None

    def __call__(self, argv, timeout=60, env=None, input=None):
        self.calls.append(argv)
        if argv[:2] == ["docker", "exec"] and "printenv" in argv:
            return subprocess.CompletedProcess(argv, 0, self.provider + "\n", "")
        if argv[:2] == ["docker", "exec"] and "test" in argv:
            return subprocess.CompletedProcess(argv, 0 if self.token else 1, "", "")
        if input is not None:
            self.written, self.token = input, True
        return subprocess.CompletedProcess(argv, 0, "", "")


def github(status):
    return lambda *args, **kwargs: SimpleNamespace(status_code=status, ok=status < 400)


def test_skipped_for_other_providers(monkeypatch):
    monkeypatch.setattr(copilot.fe, "model_provider", lambda cfg=None: "anthropic")
    ctx, _ = make_ctx(None)
    assert copilot.run(ctx).status == "skipped"


def test_already_logged_in_is_skipped(monkeypatch):
    monkeypatch.setattr(copilot.fe, "run", FakeDocker(token=True))
    ctx, _ = make_ctx(None)
    assert copilot.run(ctx).status == "skipped"


def test_container_with_old_provider_is_reported(monkeypatch):
    monkeypatch.setattr(copilot.fe, "run", FakeDocker(provider="anthropic"))
    ctx, _ = make_ctx(None)
    with pytest.raises(StepError, match="anthropic"):
        copilot.run(ctx)


def test_token_from_stdin_is_validated_written_through_stdin_and_restarts(monkeypatch):
    docker = FakeDocker()
    monkeypatch.setattr(copilot.fe, "run", docker)
    monkeypatch.setattr(copilot.requests, "get", github(200))
    monkeypatch.setattr(copilot.sys, "stdin", io.StringIO("gho_secret\n"))
    ctx, _ = make_ctx(None)
    assert copilot.run(ctx, token_stdin=True).status == "done"
    assert docker.written == "gho_secret"
    assert not any("gho_secret" in " ".join(call) for call in docker.calls)
    assert ["docker", "restart", copilot.MLFLOW_CONTAINER] in docker.calls


def test_rejected_token_is_not_written(monkeypatch):
    docker = FakeDocker()
    monkeypatch.setattr(copilot.fe, "run", docker)
    monkeypatch.setattr(copilot.requests, "get", github(401))
    monkeypatch.setattr(copilot.sys, "stdin", io.StringIO("ghp_pat"))
    ctx, _ = make_ctx(None)
    with pytest.raises(StepError, match="rejected"):
        copilot.run(ctx, token_stdin=True)
    assert docker.written is None


def test_pasted_token(monkeypatch):
    docker = FakeDocker()
    monkeypatch.setattr(copilot.fe, "run", docker)
    monkeypatch.setattr(copilot.requests, "get", github(200))
    monkeypatch.setattr(copilot, "local_editor_token", lambda: None)
    monkeypatch.setattr(ui, "ask", lambda question, default="": "token")
    monkeypatch.setattr(copilot.getpass, "getpass", lambda prompt: "gho_pasted")
    ctx, _ = make_ctx(None)
    assert copilot.run(ctx).status == "done"
    assert docker.written == "gho_pasted"


def test_local_editor_token(tmp_path, monkeypatch):
    folder = tmp_path / "github-copilot"
    folder.mkdir()
    (folder / "apps.json").write_text(json.dumps({"github.com:Iv1.abc": {"oauth_token": "gho_editor"}}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert copilot.local_editor_token() == "gho_editor"


def test_no_local_editor_token(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert copilot.local_editor_token() is None
