"""Tests for the build step. Subprocess calls are replaced by a recorder; nothing builds."""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

from steps import build  # noqa: E402
from steps.common import Context  # noqa: E402


@pytest.fixture(autouse=True)
def no_live_side_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError(f"test attempted a subprocess: {args[0] if args else kwargs}")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(os, "system", forbidden)


@pytest.fixture
def recorder(monkeypatch):
    """Replace build._run's subprocess with a recorder: records argv and env, returns the exit code."""
    calls = []
    exit_code = {"value": 0}

    def fake_subprocess_run(argv, cwd=None, env=None):
        calls.append({"argv": argv, "env": env})
        return SimpleNamespace(returncode=exit_code["value"])

    monkeypatch.setattr(build.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(build.fe, "install_path", lambda cfg=None: Path("/tmp/x/scion"))
    return SimpleNamespace(calls=calls, exit_code=exit_code)


def ctx(dry_run=False):
    lines = []
    return Context(adapter=None, cfg={}, dry_run=dry_run, log=lines.append), lines


def test_images_runs_sync_with_skip_binary(recorder):
    result = build.run_images(ctx()[0])
    assert result.status == "done"
    argv = recorder.calls[0]["argv"]
    assert argv[1].endswith("sync-factory.py")
    assert "--skip-binary" in argv and "--skip-build" not in argv


def test_scion_passes_install_path_and_env(recorder):
    build.run_scion(ctx()[0], install_path="/opt/bin/scion")
    call = recorder.calls[0]
    assert call["argv"][call["argv"].index("--install-path") + 1] == "/opt/bin/scion"
    assert call["env"]["INSTALL_PATH"] == "/opt/bin/scion"


def test_scion_defaults_to_configured_install_path(recorder):
    build.run_scion(ctx()[0])
    assert recorder.calls[0]["argv"][-1] == "/tmp/x/scion"


def test_dry_run_builds_nothing(recorder):
    images = build.run_images(ctx(dry_run=True)[0])
    scion = build.run_scion(ctx(dry_run=True)[0])
    assert images.status == scion.status == "dry-run"
    assert recorder.calls == []


def test_failed_build_is_a_step_error_with_fix(recorder):
    from steps.common import StepError

    recorder.exit_code["value"] = 2
    with pytest.raises(StepError) as error:
        build.run_images(ctx()[0])
    assert "exit 2" in error.value.message
    assert error.value.fix


def test_main_scion_subcommand_dry_run_exit_zero(recorder, capsys):
    assert build.main(["scion", "--dry-run", "--install-path", "/tmp/bin/scion"]) == 0
    assert recorder.calls == []


def test_main_failure_exits_one_and_prints_fix(recorder, capsys):
    recorder.exit_code["value"] = 1
    assert build.main(["images", "--yes"]) == 1
    err = capsys.readouterr().err
    assert "build command failed" in err and "try:" in err


def test_cli_help_exits_zero():
    with pytest.raises(SystemExit) as exit_info:
        build.main(["--help"])
    assert exit_info.value.code == 0
