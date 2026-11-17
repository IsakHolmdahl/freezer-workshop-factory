"""Shared fakes for the step tests: a stand-in for deployments.Adapter (design 4.1).

Nothing here runs a process. The autouse guard in each test module fails any test
that calls subprocess, os.system or os.exec*.
"""
import os
import subprocess
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def no_live_side_effects(monkeypatch):
    """Fail on any subprocess: step tests must never touch Docker, Scion or the network."""
    def forbidden(*args, **kwargs):
        raise AssertionError(f"test attempted a subprocess: {args[0] if args else kwargs}")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(os, "execv", forbidden)


class Result(SimpleNamespace):
    """CommandResult stand-in (design 4.1): ok when returncode == 0."""

    @property
    def ok(self):
        return self.returncode == 0


class FakeAdapter:
    name = "fake"

    def __init__(self, tmp_path=None, running=False, healthy=False, start_healthy=True, commands_fail=()):
        self.running, self.healthy, self.start_healthy = running, healthy, start_healthy
        self.started = []
        self.commands = []
        self.repo_scripts = []
        self.commands_fail = set(commands_fail)  # substrings: a command containing one fails
        self.published = False
        self.settings_file = (tmp_path / "settings.yaml") if tmp_path else None

    def status(self):
        return SimpleNamespace(running=self.running, healthy=self.healthy, detail="fake")

    def start(self, *, build=True):
        self.started.append(build)
        self.running, self.healthy = True, self.start_healthy
        return SimpleNamespace(running=True, healthy=self.start_healthy, detail="fake started")

    def scion(self, args, *, input=None, interactive=False, timeout=60):
        self.commands.append(list(args))
        failed = any(marker in " ".join(args) for marker in self.commands_fail)
        return Result(argv=list(args), returncode=1 if failed else 0, stderr="boom" if failed else "")

    def run_repo_script(self, script, args):
        self.repo_scripts.append((script, list(args)))
        return Result(argv=[script, *args], returncode=0)

    def prepare_publish(self):
        self.published = True

    def settings_path(self):
        return self.settings_file

    def render_settings(self, current):
        return {**current, "rendered": True}

    def service_endpoints(self):
        return SimpleNamespace(hub_human="http://hub.test", hub_agent="http://hub.test", broker=None,
                               mlflow="http://mlflow.test", dolt="mysql://x")


def make_ctx(adapter, dry_run=False, assume_yes=False):
    from steps.common import Context

    lines = []
    return Context(adapter=adapter, cfg={}, dry_run=dry_run, assume_yes=assume_yes, log=lines.append), lines
