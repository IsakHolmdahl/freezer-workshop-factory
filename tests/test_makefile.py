"""Exercise Make in an isolated checkout with a fake bootstrap (no pip or Docker)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def checkout(tmp_path):
    if not shutil.which("make"):
        pytest.skip("make is not installed")
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "Makefile").write_text((root / "Makefile").read_text())
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/docker-python.sh").write_text((root / "scripts/docker-python.sh").read_text())
    (tmp_path / "scripts/requirements.txt").write_text("")
    (tmp_path / "setup.py").write_text(
        "import pathlib, sys\n"
        "assert sys.argv[1:] == ['--venv-only']\n"
        "with open('bootstraps', 'a') as f: f.write('bootstrap\\n')\n"
        "p = pathlib.Path('.venv/bin/python')\n"
        "p.parent.mkdir(parents=True, exist_ok=True)\n"
        "if not p.exists(): p.symlink_to(sys.executable)\n")
    for name in ("setup", "doctor", "cleanup", "scion_install"):
        (tmp_path / f"scripts/{name}.py").write_text(
            "import json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    return tmp_path


def run_make(checkout, *args):
    return subprocess.run(["make", "--no-print-directory", f"PYTHON={sys.executable}", *args],
                          cwd=checkout, text=True, capture_output=True, check=True).stdout


@pytest.mark.parametrize(("target", "expected"), [
    ("setup", []), ("setup-auto", ["--yes"]),
    ("setup-dry-run", ["--yes", "--dry-run"]),
    ("setup-services", ["--only", "configuration,services,endpoints"]),
    ("setup-start", ["--only", "scion_settings,scion_start"]),
    ("setup-scion", ["--only", "scion_cli,build_scion"]),
    ("setup-cli", []),
    ("setup-images", ["--only", "build_images"]),
    ("setup-github", ["--only", "secrets,hub_wiring"]),
    ("setup-publish", ["--only", "hub_wiring"]),
    ("doctor", []), ("doctor-quick", ["--quick"]), ("doctor-fix", ["--fix"]),
    ("cleanup", []), ("cleanup-dry-run", ["--dry-run"]),
    ("cleanup-auto", ["--yes"]), ("cleanup-volumes", ["--yes", "--volumes"]),
])
def test_named_commands_bootstrap_and_run_expected_options(checkout, target, expected):
    output = run_make(checkout, target)
    assert json.loads(output) == expected
    assert (checkout / "bootstraps").read_text() == "bootstrap\n"
    run_make(checkout, target)
    assert (checkout / "bootstraps").read_text() == "bootstrap\n"


def test_github_rotation_replaces_then_publishes(checkout):
    steps = checkout / "scripts/steps"
    steps.mkdir()
    (steps / "secrets.py").write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    output = run_make(checkout, "setup-github-rotate")
    assert [json.loads(line) for line in output.splitlines()] == [["--replace"], ["--only", "hub_wiring"]]


def test_start_applies_service_changes_then_starts_the_hub(checkout):
    steps = checkout / "scripts/steps"
    steps.mkdir()
    (steps / "services.py").write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    output = run_make(checkout, "start")
    assert [json.loads(line) for line in output.splitlines()] == [
        ["--force"], ["--only", "scion_settings,scion_start"]]


def test_venv_refreshes_requirements_and_recovers_missing_python(checkout):
    run_make(checkout, "venv")
    stamp = checkout / ".venv/.requirements-installed"
    os.utime(stamp, (1, 1))
    run_make(checkout, "venv")
    assert (checkout / "bootstraps").read_text().count("bootstrap") == 2
    (checkout / ".venv/bin/python").unlink()
    run_make(checkout, "venv")
    assert (checkout / "bootstraps").read_text().count("bootstrap") == 3


def test_help_needs_no_environment(checkout):
    assert "make setup" in run_make(checkout)
    assert not (checkout / ".venv").exists()


@pytest.mark.parametrize("answer", ["y\n", "n\n", ""])
def test_missing_python_offers_docker_and_requires_acceptance(checkout, answer):
    fakebin = checkout / "fakebin"
    fakebin.mkdir()
    docker = fakebin / "docker"
    docker.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > docker-args\n")
    docker.chmod(0o755)
    env = {**os.environ, "PATH": f"{fakebin}:{os.environ['PATH']}"}
    result = subprocess.run(["make", "--no-print-directory", "PYTHON=missing-python", "setup"],
                            cwd=checkout, env=env, input=answer, text=True, capture_output=True)
    assert "Docker python:3.12-alpine?" in result.stdout
    if answer == "y\n":
        assert result.returncode == 0, result.stderr
        args = (checkout / "docker-args").read_text().splitlines()
        assert "python:3.12-alpine" in args
        assert "ASF_MAKE_TARGET=setup" in args
        assert "asf-python312-alpine" in " ".join(args)
    else:
        assert result.returncode != 0
        assert not (checkout / "docker-args").exists()
