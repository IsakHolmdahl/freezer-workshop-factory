"""Tests for the guided installer, run with: .venv/bin/python -m pytest tests/test_setup.py"""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

import doctor  # noqa: E402
import factory_env as fe  # noqa: E402


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A fake repo root with the real asf.yaml and templates."""
    for name in ("asf.yaml", ".env.template", ".env.secrets.template"):
        (tmp_path / name).write_text((fe.ROOT / name).read_text())
    for attr, name in (("CONFIG_FILE", "asf.yaml"), ("LOCAL_CONFIG_FILE", "asf.local.yaml"),
                       ("ENV_FILE", ".env"), ("ENV_TEMPLATE", ".env.template"),
                       ("SECRETS_FILE", ".env.secrets"), ("SECRETS_TEMPLATE", ".env.secrets.template")):
        monkeypatch.setattr(fe, attr, tmp_path / name)
    for key in list(__import__("os").environ):
        if key.startswith(("SCION_", "AGENT_GATEWAY_", "DOLT_")):
            monkeypatch.delenv(key)
    # No Dolt volume yet, no git identity.
    monkeypatch.setattr(fe, "run", lambda argv, **kw: fe.subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr("deployments.stack.ComposeStack.volume", lambda self, name: None)
    return tmp_path










def test_doctor_config_flags_missing_files(repo):
    results = {r.title: r for r in doctor.check_config({})}
    assert results[".env exists"].status == doctor.FAIL
    assert results["MODEL_PROVIDER_API_KEY set for provider anthropic"].status == doctor.FAIL
    assert results[".env matches asf.yaml"].status == doctor.WARN
    assert results["agent gateway configured"].status == doctor.OK
    assert results["DOLT_DB_PASSWORD set in .env.secrets"].status == doctor.WARN


def test_seed_gateway_endpoint_overrides(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "seed_gateway", fe.ROOT / "mlflow" / "bootstrap" / "seed_gateway.py")
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    assert set(seed.endpoints()) == {"small", "medium", "large"}
    monkeypatch.setenv("MLFLOW_ENDPOINT_LARGE", "a:0.25,b:0.75")
    monkeypatch.setenv("MLFLOW_ENDPOINT_SMALL", "x,y")
    assert seed.endpoints()["large"] == {"a": 0.25, "b": 0.75}
    assert seed.endpoints()["small"] == {"x": 0.5, "y": 0.5}


def test_scion_settings_first_start(tmp_path):
    import scion_settings
    import yaml
    path = tmp_path / "scion" / "settings.yaml"
    values = {"SCION_ADMIN_EMAIL": "dev@example.com", "SCION_SERVER_AUTH_DISPLAYNAME": "Dev"}
    # first start: complete file without overrides
    _, changed, backup = scion_settings.ensure(values, path)
    data = yaml.safe_load(path.read_text())
    assert changed and backup is None
    assert data["hub"]["endpoint"] == "http://localhost:8080"
    assert data["server"]["auth"] == {"email": "dev@example.com", "display_name": "Dev"}
    assert data["default_harness_config"] == "asf-opencode"
    assert "harness_overrides" not in data["profiles"]["local"]
    assert data["shared_dirs"] == [{"name": "scratchpad"}, {"name": "build-cache"}]
    assert data["telemetry"]["enabled"] is True
    assert data["telemetry"]["cloud"] == {
        "enabled": True,
        "endpoint": "localhost:4317",
        "protocol": "grpc",
        "tls": {"enabled": False},
    }
    # Scion and the user add their own values; they are kept
    data["server"]["broker"] = {"broker_id": "abc"}
    data["default_template"] = "researcher"
    data["shared_dirs"] = [{"name": "docs", "read_only": True}, {"name": "scratchpad"}]
    data["profiles"]["local"]["harness_overrides"] = {
        "custom": {"env": {"A": "1"}}}
    path.write_text(yaml.safe_dump(data))
    _, changed, backup = scion_settings.ensure(values, path)
    data = yaml.safe_load(path.read_text())
    assert changed and backup.is_file()
    assert data["server"]["broker"] == {"broker_id": "abc"}
    assert data["default_template"] == "researcher"
    assert data["shared_dirs"] == [{"name": "docs", "read_only": True}, {"name": "scratchpad"},
                                   {"name": "build-cache"}]
    assert data["profiles"]["local"]["harness_overrides"] == {"custom": {"env": {"A": "1"}}}
    # idempotent
    assert scion_settings.ensure(values, path)[1] is False
    # image_registry follows asf.yaml even when the file has another value
    data = yaml.safe_load(path.read_text())
    data["image_registry"] = "old.example"
    path.write_text(yaml.safe_dump(data))
    scion_settings.ensure(values, path)
    assert yaml.safe_load(path.read_text())["image_registry"] == fe.get("scion.image_registry")
    # rootless Docker socket for Testcontainers is mounted when present
    import socket as sockmod
    import tempfile
    # A short path: macOS limits AF_UNIX socket paths to 104 bytes, pytest's tmp_path is longer.
    sock_dir = Path(tempfile.mkdtemp(prefix="asf", dir="/tmp"))
    tc_sock = sock_dir / "r.sock"
    srv = sockmod.socket(sockmod.AF_UNIX, sockmod.SOCK_STREAM)
    try:
        srv.bind(str(tc_sock))
        scion_settings.ensure(values, path, tc_socket=tc_sock)
        vols = yaml.safe_load(path.read_text())["profiles"]["local"]["volumes"]
        assert vols == [{"source": str(tc_sock), "target": scion_settings.TESTCONTAINERS_TARGET_SOCKET}]
    finally:
        srv.close()
    tc_sock.unlink()
    sock_dir.rmdir()
    scion_settings.ensure(values, path, tc_socket=tc_sock)
    assert "volumes" not in yaml.safe_load(path.read_text())["profiles"]["local"]


def test_scion_harness_default_replaces_upstream_and_follows_asf_config(tmp_path, monkeypatch):
    import scion_settings
    original_get = fe.get
    monkeypatch.setattr(fe, "get", lambda key, *args, **kwargs:
                        "asf-opencode-python" if key == "scion.default_harness_config"
                        else original_get(key, *args, **kwargs))
    current = {"default_harness_config": "antigravity", "default_template": "custom"}
    updated = scion_settings.render(current, {})
    assert updated["default_harness_config"] == "asf-opencode-python"
    assert updated["default_template"] == "custom"
    assert current["default_harness_config"] == "antigravity"


def test_git_config_check_matches_scion_policy(tmp_path):
    import subprocess
    import git_config_check as gcc
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for key, value in (("branch.feat/x.vscode-merge-base", "origin/main"), ("remote.origin.gh-resolved", "base"),
                       ("core.sshCommand", "ssh -i k"), ("branch.main.merge", "refs/heads/main"),
                       ("color.status.added", "green"), ("advice.detachedHead", "false")):
        subprocess.run(["git", "-C", str(tmp_path), "config", key, value], check=True)
    keys = gcc.refused_keys(tmp_path)
    assert keys == ["branch.feat/x.vscode-merge-base", "core.sshcommand", "remote.origin.gh-resolved"]
    assert [k for k in keys if gcc.is_tool_metadata(k)] == ["branch.feat/x.vscode-merge-base",
                                                             "remote.origin.gh-resolved"]
    assert gcc.remove(tmp_path, "branch.feat/x.vscode-merge-base")
    assert "branch.feat/x.vscode-merge-base" not in gcc.refused_keys(tmp_path)


def test_doctor_fix_runs_only_failed_autofixes(capsys):
    calls = []
    sections = [("A", [doctor.Result(doctor.FAIL, "broken", autofix=lambda: calls.append("a") or "done"),
                       doctor.Result(doctor.OK, "fine", autofix=lambda: calls.append("b") or "no"),
                       doctor.Result(doctor.FAIL, "fails", autofix=lambda: (_ for _ in ()).throw(RuntimeError("x")))])]
    assert doctor.apply_fixes(sections) == 1
    assert calls == ["a"]
    out = capsys.readouterr().out
    assert "fixed  broken: done" in out and "failed fails: x" in out




def test_root_setup_bootstraps_venv_when_ensurepip_missing(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("root_setup", fe.ROOT / "setup.py")
    root_setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(root_setup)

    venv_dir = tmp_path / ".venv"
    python_bin = venv_dir / "bin" / "python"
    reqs = tmp_path / "requirements.txt"
    reqs.write_text("requests\n")

    create_calls = []

    def fake_create(target, with_pip=False):
        create_calls.append(with_pip)
        if with_pip:
            raise RuntimeError("ensurepip is not available")
        (Path(target) / "bin").mkdir(parents=True, exist_ok=True)
        python_bin.write_text("#!/bin/sh\n")

    pip_ready = {"ok": False}
    monkeypatch.setattr(root_setup.venv, "create", fake_create)
    monkeypatch.setattr(root_setup, "has_pip", lambda p=python_bin: pip_ready["ok"])

    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def read(self):
            return b"# get-pip"

    monkeypatch.setattr(root_setup.urllib.request, "urlopen", lambda *a, **kw: FakeResp())
    monkeypatch.setattr(root_setup.shutil, "which", lambda name: None)

    def fake_run(argv, **kw):
        if len(argv) >= 2 and str(argv[1]).endswith("-get-pip.py"):
            pip_ready["ok"] = True
        return fe.subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(root_setup.subprocess, "run", fake_run)
    root_setup.ensure_venv(venv_dir, python_bin, reqs)
    assert create_calls == [True, False]
    assert pip_ready["ok"] is True


def test_root_setup_venv_only_does_not_run_installer(monkeypatch):
    spec = importlib.util.spec_from_file_location("root_setup", fe.ROOT / "setup.py")
    root_setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(root_setup)
    calls = []
    monkeypatch.setattr(root_setup, "ensure_venv", lambda: calls.append("bootstrap"))
    monkeypatch.setattr(sys, "argv", ["setup.py", "--venv-only"])

    def forbidden(*args):
        pytest.fail("--venv-only must not launch the installer")

    monkeypatch.setattr(root_setup.os, "execv", forbidden)
    root_setup.main()
    assert calls == ["bootstrap"]

