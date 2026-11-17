"""Tests for scripts/cleanup.py: the plan, the default selection and the CLI.

No test touches Docker, Scion or the filesystem outside tmp_path: the guard fixture
fails any test that calls subprocess, os.system or os.exec*, and every process
boundary of cleanup.py is replaced with a fake.
"""
import io
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

import cleanup  # noqa: E402
from deployments.base import CommandResult, Resource  # noqa: E402


@pytest.fixture(autouse=True)
def no_live_side_effects(monkeypatch):
    """Fail on any subprocess: cleanup tests must never touch Docker, Scion or the network."""
    def forbidden(*args, **kwargs):
        raise AssertionError(f"test attempted a subprocess: {args[0] if args else kwargs}")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(os, "execv", forbidden)
    monkeypatch.setattr(cleanup, "run", lambda argv, timeout=300: CommandResult(list(argv), 1))
    monkeypatch.setattr(cleanup, "docker_ok", lambda: False)


def fake_part(key, *, destructive=False, fail=False, calls=None):
    def remove():
        if calls is not None:
            calls.append(key)
        if fail:
            raise RuntimeError(f"{key} broke")
    return cleanup.Part(key, f"title {key}", [key], "", remove, destructive=destructive)


def test_default_selection_keeps_data_volumes_unless_asked():
    parts = [fake_part("config"), fake_part("volumes", destructive=True)]
    assert [p.key for p in cleanup.default_selection(parts, volumes=False)] == ["config"]
    assert [p.key for p in cleanup.default_selection(parts, volumes=True)] == ["config", "volumes"]


def test_execute_dry_run_removes_nothing():
    calls = []
    failed = cleanup.execute([fake_part("a", calls=calls)], dry_run=True, log=lambda _: None)
    assert failed == [] and calls == []


def test_execute_continues_after_a_failure_and_reports_it():
    calls, lines = [], []
    failed = cleanup.execute([fake_part("a", fail=True, calls=calls), fake_part("b", calls=calls)],
                             log=lines.append)
    assert calls == ["a", "b"]
    assert failed == ["a"]
    assert any("failed: a broke" in line for line in lines)


def test_check_raises_with_the_command_output():
    with pytest.raises(RuntimeError, match="no such network"):
        cleanup.check(CommandResult(["docker"], 1, "", "no such network"))
    cleanup.check(CommandResult(["docker"], 0))  # success does not raise


class FakeStack:
    def __init__(self, rows):
        self.rows = rows
        self.downs = []

    def ps(self, all=False):
        return self.rows

    def down(self, **kwargs):
        self.downs.append(kwargs)
        return CommandResult(["docker", "compose", "down"], 0)


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """Point every path cleanup.py touches into tmp_path."""
    monkeypatch.setattr(cleanup, "SCION_HOME", tmp_path / "scion-home")
    monkeypatch.setattr(cleanup, "INSTALL_PATH", tmp_path / "bin" / "scion")
    monkeypatch.setattr(cleanup, "ROOT", tmp_path / "repo")
    monkeypatch.setattr(cleanup, "LocalAdapter", lambda cfg: SimpleNamespace(
        status=lambda: SimpleNamespace(running=False), stop=lambda: None))
    monkeypatch.setattr(cleanup, "built_images", lambda: [])
    monkeypatch.setattr(cleanup, "agent_containers", lambda: [])
    monkeypatch.setattr(cleanup, "check_network", lambda: False)
    (tmp_path / "repo").mkdir()
    return tmp_path


def test_plan_without_docker_lists_only_files(isolated_paths):
    (isolated_paths / "repo" / ".env").write_text("X=1")
    (isolated_paths / "scion-home").mkdir()
    keys = [p.key for p in cleanup.plan({}, has_docker=False)]
    assert keys == ["scion-home", "config"]


def test_plan_with_docker_orders_containers_volumes_then_network_and_marks_volumes_destructive(
        isolated_paths, monkeypatch):
    stack = FakeStack([{"Name": "asf-dolt-1", "Service": "dolt"}])
    monkeypatch.setattr(cleanup, "core_project", lambda: stack)
    volumes = [Resource("volume", name, True, lambda: CommandResult([], 0))
               for name in ("asf_dolt-data", "asf_pgdata")]
    monkeypatch.setattr(cleanup, "core_volumes", lambda project: volumes)
    parts = cleanup.plan({}, has_docker=True)
    assert [p.key for p in parts] == ["stack", "volumes"]
    volumes = parts[-1]
    assert volumes.destructive and volumes.items == ["asf_dolt-data", "asf_pgdata"]
    assert parts[0].items == ["asf-dolt-1"]


def test_stack_removal_runs_compose_down_without_volumes(isolated_paths, monkeypatch):
    stack = FakeStack([{"Name": "asf-dolt-1", "Service": "dolt"}])
    monkeypatch.setattr(cleanup, "core_project", lambda: stack)
    monkeypatch.setattr(cleanup, "core_volumes", lambda project: [])
    (stack_part,) = [p for p in cleanup.plan({}, has_docker=True) if p.key == "stack"]
    stack_part.remove()
    assert stack.downs == [{}]  # no volumes=True: data is removed only through the volumes part


def test_cli_dry_run_removes_nothing(isolated_paths, monkeypatch, capsys):
    (isolated_paths / "repo" / ".env").write_text("X=1")
    monkeypatch.setattr(cleanup, "docker_ok", lambda: False)
    monkeypatch.setattr(cleanup, "remove", lambda p: pytest.fail(f"dry-run removed {p}"))
    assert cleanup.main(["--dry-run"]) == 0
    assert (isolated_paths / "repo" / ".env").exists()
    assert "Dry run: nothing was removed." in capsys.readouterr().out


def test_cli_yes_keeps_volumes_and_says_so(isolated_paths, monkeypatch, capsys):
    (isolated_paths / "repo" / ".env").write_text("X=1")
    volumes = []
    parts = [fake_part("config"), fake_part("volumes", destructive=True, calls=volumes)]
    monkeypatch.setattr(cleanup, "plan", lambda cfg=None, has_docker=None: parts)
    assert cleanup.main(["--yes"]) == 0
    assert volumes == []
    assert "Kept (add --volumes" in capsys.readouterr().out


def test_cli_yes_volumes_deletes_them(isolated_paths, monkeypatch):
    volumes = []
    parts = [fake_part("config"), fake_part("volumes", destructive=True, calls=volumes)]
    monkeypatch.setattr(cleanup, "plan", lambda cfg=None, has_docker=None: parts)
    assert cleanup.main(["--yes", "--volumes"]) == 0
    assert volumes == ["volumes"]


def test_cli_skip_keeps_named_parts(isolated_paths, monkeypatch):
    calls = []
    parts = [fake_part("config", calls=calls), fake_part("images", calls=calls)]
    monkeypatch.setattr(cleanup, "plan", lambda cfg=None, has_docker=None: parts)
    assert cleanup.main(["--yes", "--skip", "config"]) == 0
    assert calls == ["images"]


def test_cli_without_terminal_needs_yes_or_dry_run(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit) as exit_info:
        cleanup.main([])
    assert exit_info.value.code == 2


def test_cli_returns_nonzero_when_a_part_fails(isolated_paths, monkeypatch, capsys):
    parts = [fake_part("config", fail=True)]
    monkeypatch.setattr(cleanup, "plan", lambda cfg=None, has_docker=None: parts)
    assert cleanup.main(["--yes"]) == 1
    assert "these parts failed: config" in capsys.readouterr().out


def test_help_exits_zero_and_documents_volumes(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cleanup.main(["--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "--volumes" in out and "--dry-run" in out
