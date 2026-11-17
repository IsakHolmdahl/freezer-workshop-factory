"""Adapter contract and ComposeStack argv: no real process is ever started (subprocess is recorded)."""
import subprocess
import io
from pathlib import Path

import pytest

import factory_env as fe
from deployments import base
from deployments.base import DeployError, is_read_only_scion, run_command
from deployments.stack import ComposeStack, check_version, core_stack


def test_publish_stream_shows_item_counts_and_retains_failure_details(monkeypatch):
    class Process:
        stdout = io.StringIO("verbose output\nPublish [1/3] Skill complete: example\nerror details\n")
        def wait(self, timeout=None):
            return 1
        def poll(self):
            return 1
    monkeypatch.setattr(base.subprocess, "Popen", lambda *a, **k: Process())
    shown = []
    result = run_command(["python", "scripts/sync-factory.py"], log=shown.append)
    assert shown == ["Publish [1/3] Skill complete: example"]
    assert result.returncode == 1
    assert "verbose output" in result.stdout and "error details" in result.stdout


@pytest.fixture(autouse=True)
def no_real_processes(monkeypatch):
    """Every subprocess call is recorded and answered with a canned success."""
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(base.subprocess, "run", fake_run)
    monkeypatch.setattr(fe, "scion_commit", lambda: "abc123")
    return calls


def test_compose_argv_orders_project_files_and_profiles(tmp_path):
    stack = ComposeStack([tmp_path / "a.yaml", tmp_path / "b.yaml"], profiles=["scion"],
                         project_name="asf-containers")
    assert stack.argv("up", "-d") == [
        "docker", "compose", "--project-name", "asf-containers",
        "-f", str(tmp_path / "a.yaml"), "-f", str(tmp_path / "b.yaml"),
        "--profile", "scion", "up", "-d"]


def test_core_stack_has_no_scion_profile():
    local = core_stack({}).argv("config")
    assert "--profile" not in local
    assert local[-1] == "config" and str(fe.ROOT / "compose.yaml") in local


def test_dry_run_logs_mutating_command_and_executes_nothing(no_real_processes):
    lines = []
    stack = ComposeStack([Path("compose.yaml")], dry_run=True, log=lines.append)
    result = stack.up(["dolt"])
    assert result.ok
    assert no_real_processes == []
    assert lines and lines[0].startswith("$ docker compose")


def test_dry_run_still_runs_read_only_queries(no_real_processes):
    stack = ComposeStack([Path("compose.yaml")], dry_run=True, log=lambda _: None)
    stack.services()
    assert no_real_processes[0][-2:] == ["config", "--services"] or no_real_processes[0][-1] == "--services"


def test_run_command_reports_missing_binary_without_raising(monkeypatch):
    def missing(argv, **kwargs):
        raise FileNotFoundError("nope")
    monkeypatch.setattr(base.subprocess, "run", missing)
    result = run_command(["no-such-binary"])
    assert result.returncode == 127 and not result.ok


def test_run_command_reports_timeout_without_raising(monkeypatch):
    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1)
    monkeypatch.setattr(base.subprocess, "run", slow)
    result = run_command(["sleepy"], timeout=1)
    assert result.returncode == 124 and "timed out" in result.stderr


def test_ps_parses_array_and_line_json(monkeypatch):
    outputs = iter(['[{"Service": "scion"}]', '{"Service": "a"}\n{"Service": "b"}\n'])
    def fake(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, next(outputs), "")
    monkeypatch.setattr(base.subprocess, "run", fake)
    stack = ComposeStack([Path("compose.yaml")], log=lambda _: None)
    assert stack.ps(all=True) == [{"Service": "scion"}]
    assert [r["Service"] for r in stack.ps()] == ["a", "b"]


def test_check_version_requires_compose_2_24():
    check_version((2, 24, 0))
    check_version((2, 30))
    with pytest.raises(DeployError, match="too old") as error:
        check_version((2, 23, 1))
    assert error.value.fix
    with pytest.raises(DeployError, match="not available"):
        check_version(None)


def test_read_only_scion_classification():
    assert is_read_only_scion(["--non-interactive", "hub", "list"])
    assert is_read_only_scion(["server", "status", "--json"])
    assert not is_read_only_scion(["templates", "import", "x"])
    assert not is_read_only_scion(["hub", "env", "set", "DOLT_URL", "x"])


def test_get_adapter_is_always_local():
    from deployments import get_adapter
    from deployments.local import LocalAdapter

    assert isinstance(get_adapter({}), LocalAdapter)
    assert isinstance(get_adapter({}, name="local"), LocalAdapter)
    with pytest.raises(DeployError, match="unknown"):
        get_adapter({}, name="kubernetes")


def test_scion_is_dry_run_safe_for_writes(no_real_processes):
    from deployments.local import LocalAdapter
    lines = []
    adapter = LocalAdapter({}, dry_run=True, log=lines.append)
    adapter.scion(["templates", "import", "x"])
    assert no_real_processes == [] and lines[0].startswith("$ scion templates import")
    adapter.scion(["server", "status", "--json"])
    assert any(call[:3] == ["scion", "server", "status"] for call in no_real_processes)


def test_secrets_are_sent_through_stdin_not_argv(no_real_processes):
    from deployments.local import LocalAdapter
    adapter = LocalAdapter({}, log=lambda _: None)
    adapter.scion(["hub", "secret", "set", "--scope", "hub", "KEY"], input="s3cret")
    assert not any("s3cret" in arg for call in no_real_processes for arg in call)
