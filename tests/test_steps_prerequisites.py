"""Tests for the prerequisites step. Tool and Docker probes are replaced by fakes.

The autouse guard from test_steps_common is repeated here: any real process fails the test.
"""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

from steps import prerequisites  # noqa: E402
from steps.common import Context, StepError  # noqa: E402


@pytest.fixture(autouse=True)
def no_live_side_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError(f"test attempted a subprocess: {args[0] if args else kwargs}")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(os, "system", forbidden)


def ctx(dry_run=False):
    lines = []
    return Context(adapter=None, cfg={}, dry_run=dry_run, log=lines.append), lines


def completed(stdout="", returncode=0, stderr=""):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


@pytest.fixture
def healthy(monkeypatch, tmp_path):
    """All tools present, Docker and Compose new enough, Scion source present."""
    monkeypatch.setattr(prerequisites.fe, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(prerequisites.fe, "SCION_SRC", tmp_path)
    (tmp_path / "Makefile").write_text("all:\n")
    replies = {
        ("go", "version"): completed("go version go1.26.1 linux/amd64\n"),
        ("docker", "info"): completed("27.0\n"),
        ("docker", "compose", "version"): completed("v2.29.1\n"),
    }
    monkeypatch.setattr(prerequisites.scion_source, "ensure_source", lambda **kw: False)

    def fake_run(argv, timeout=60, env=None, input=None):
        for key, reply in replies.items():
            if tuple(argv[:len(key)]) == key:
                return reply
        raise AssertionError(f"unexpected command {argv}")

    monkeypatch.setattr(prerequisites.fe, "run", fake_run)
    return replies


def test_all_present_is_skipped(healthy):
    result = prerequisites.run(ctx()[0])
    assert result.status == "skipped"


def test_missing_tools_are_listed_with_hints(healthy, monkeypatch):
    monkeypatch.setattr(prerequisites.fe, "which", lambda name: None if name in ("make", "npm") else "/x")
    with pytest.raises(StepError) as error:
        prerequisites.run(ctx()[0])
    assert "make, npm" in error.value.message
    assert any("apt install make" in line for line in error.value.fix)
    assert any("NEW terminal" in line for line in error.value.fix)


def test_old_go_is_rejected(healthy):
    healthy[("go", "version")] = completed("go version go1.25.3 linux/amd64\n")
    with pytest.raises(StepError) as error:
        prerequisites.run(ctx()[0])
    assert "1.26" in error.value.message


def test_docker_permission_denied_has_group_fix(healthy):
    healthy[("docker", "info")] = completed("", 1, "permission denied while trying to connect")
    with pytest.raises(StepError) as error:
        prerequisites.run(ctx()[0])
    assert "permission denied" in error.value.message
    assert any("usermod" in line for line in error.value.fix)


def test_docker_not_running_has_start_fix(healthy):
    healthy[("docker", "info")] = completed("", 1, "cannot connect to the daemon")
    with pytest.raises(StepError) as error:
        prerequisites.run(ctx()[0])
    assert "not reachable" in error.value.message
    assert any("systemctl start docker" in line for line in error.value.fix)


def test_compose_older_than_2_24_is_rejected(healthy):
    healthy[("docker", "compose", "version")] = completed("v2.23.3\n")
    with pytest.raises(StepError) as error:
        prerequisites.run(ctx()[0])
    assert "2.24" in error.value.message


def test_missing_scion_source_is_initialised_and_dry_run_does_not_run_it(healthy, monkeypatch, tmp_path):
    (tmp_path / "Makefile").unlink()
    step_context, lines = ctx(dry_run=True)
    result = prerequisites.run(step_context)
    assert result.status == "dry-run"
    assert any("git submodule update --init scion" in line for line in lines)


def test_missing_scion_source_runs_submodule_update_once(healthy, monkeypatch, tmp_path):
    (tmp_path / "Makefile").unlink()
    calls = []

    def fake_run(argv, timeout=60, env=None, input=None):
        calls.append(argv)
        if argv[:2] == ["git", "submodule"]:
            (tmp_path / "Makefile").write_text("all:\n")
            return completed()
        return healthy[tuple(argv[:3])] if tuple(argv[:3]) in healthy else healthy[tuple(argv[:2])]

    def select(**kwargs):
        fake_run(["git", "submodule", "update", "--init", "scion"])
        return True

    monkeypatch.setattr(prerequisites.scion_source, "ensure_source", select)
    result = prerequisites.run(ctx()[0])
    assert result.status == "done"
    assert ["git", "submodule", "update", "--init", "scion"] in calls


def test_cli_help_exits_zero():
    with pytest.raises(SystemExit) as exit_info:
        prerequisites.main(["--help"])
    assert exit_info.value.code == 0


@pytest.mark.parametrize("platform_name,os_id,command", [
    ("linux", "ubuntu", ["sudo", "apt", "install", "-y", "jq"]),
    ("darwin", "", ["brew", "install", "jq"]),
    ("linux", "fedora", None),
])
def test_jq_platform_install_command(monkeypatch, platform_name, os_id, command):
    monkeypatch.setattr(prerequisites.sys, "platform", platform_name)
    monkeypatch.setattr(prerequisites.platform, "freedesktop_os_release", lambda: {"ID": os_id})
    assert prerequisites.jq_install_command() == command


def test_jq_install_requires_confirmation_and_rechecks_path(monkeypatch):
    installed = []
    monkeypatch.setattr(prerequisites.fe, "which", lambda tool: "/bin/tool" if tool != "jq" or installed else None)
    command = ["sudo", "apt", "install", "-y", "jq"]
    monkeypatch.setattr(prerequisites, "jq_install_command", lambda: command)
    monkeypatch.setattr(prerequisites.ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(prerequisites.subprocess, "run", lambda argv, **kw: installed.append(argv) or completed())
    prerequisites.ensure_jq(ctx()[0])
    assert installed == [command]


def test_jq_manual_install_prompts_then_rechecks(monkeypatch):
    prompted = []
    monkeypatch.setattr(prerequisites.fe, "which", lambda tool: "/bin/jq" if prompted else None)
    monkeypatch.setattr(prerequisites, "jq_install_command", lambda: None)
    monkeypatch.setattr(prerequisites.ui, "ask", lambda *a: prompted.append(a))
    prerequisites.ensure_jq(ctx()[0])
    assert prompted


@pytest.mark.parametrize("mode", ["decline", "yes", "dry-run"])
def test_jq_missing_does_not_install_without_consent(monkeypatch, mode):
    monkeypatch.setattr(prerequisites.fe, "which", lambda tool: None)
    monkeypatch.setattr(prerequisites, "jq_install_command", lambda: ["brew", "install", "jq"])
    monkeypatch.setattr(prerequisites.ui, "confirm", lambda *a, **k: False)
    context, lines = ctx(dry_run=mode == "dry-run")
    context.assume_yes = mode == "yes"
    if mode == "dry-run":
        prerequisites.ensure_jq(context)
        assert lines
    else:
        with pytest.raises(StepError):
            prerequisites.ensure_jq(context)
