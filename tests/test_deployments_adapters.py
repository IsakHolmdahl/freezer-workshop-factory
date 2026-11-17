"""The local adapter, and factory_env's removed routing: all process calls are recorded."""
import json
import subprocess

import pytest

import factory_env as fe
from deployments import base
from deployments.base import DeployError
from deployments.local import LocalAdapter


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    def fake_run(argv, **kwargs):
        recorded.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(base.subprocess, "run", fake_run)
    return recorded


def ps_rows(monkeypatch, rows):
    def fake(argv, **kwargs):
        if "ps" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps(rows), "")
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(base.subprocess, "run", fake)


def test_local_start_is_noop_when_running(monkeypatch, calls):
    monkeypatch.setattr(base.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, '{"daemonRunning": true}', ""))
    adapter = LocalAdapter({}, log=lambda _: None)
    assert adapter.start().running


def test_local_dry_run_start_does_not_start_server(monkeypatch, calls):
    monkeypatch.setattr(base.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, '{"daemonRunning": false}', ""))
    lines = []
    LocalAdapter({}, dry_run=True, log=lines.append).start()
    assert any("server" in line and "start" in line for line in lines)


def test_factory_env_no_longer_rewrites_scion_commands(monkeypatch, calls):
    monkeypatch.setattr(fe, "deployment", lambda cfg=None: "containers")
    monkeypatch.delenv("ASF_CONTAINER_INTERNAL", raising=False)
    fe.run(["scion", "hub", "list"])
    assert calls == [["scion", "hub", "list"]]


def test_factory_env_compose_file_is_the_new_layout():
    assert fe.COMPOSE_FILE.name == "compose.yaml"
