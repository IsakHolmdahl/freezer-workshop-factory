"""Installed shell extensions against a persistent fake CLI; no Hub or containers."""
import json
import os
import subprocess
import sys

import pytest

from scion_install import install_scion


FAKE = '''
import json, os, sys
from pathlib import Path
state = Path(os.environ["FAKE_HUB"])
data = json.loads(state.read_text())
args = sys.argv[1:]
if args[:1] == ["--non-interactive"]: args.pop(0)
if args[-2:] == ["--format", "json"]: del args[-2:]
if args[:3] == ["hub", "env", "clear"] and os.environ.get("FAKE_CLEAR_ERROR"):
    print(os.environ["FAKE_CLEAR_ERROR"], file=sys.stderr)
    sys.exit(1)
data["calls"].append(args)
out = {}
text = None
def project(): return args[args.index("--project") + 1]
if args[:2] == ["hub", "projects"]:
    out = [{"slug": s, "id": i} for s, i in data["projects"].items()]
elif args[:2] == ["hub", "brokers"]:
    out = [{"id":"broker-id", "name":"hosted-broker", "status":"online"}]
elif args[:3] == ["hub", "project", "info"]:
    out = {"providers": []}
elif args[:3] == ["hub", "project", "create"]:
    slug = args[args.index("--slug") + 1]
    data["projects"][slug] = "new-id"
    out = {"id":"new-id"}
elif args[:3] == ["hub", "messaging", "get"]:
    text = "Hub Messaging Settings (revision 0):\\n  cross_project_messaging_enabled: " + str(data["switch"]).lower()
elif args[:3] == ["hub", "messaging", "set"]:
    data["switch"] = "--cross-project-enabled=true" in args
elif args[:3] == ["project", "messaging", "get"]:
    text = "Project Messaging Policy (revision 0):\\n  cross_project_inbound: " + data["policies"][project()]
elif args[:3] == ["project", "messaging", "set"]:
    data["policies"][project()] = args[args.index("--policy") + 1]
elif args[:1] == ["list"]:
    out = [{"slug":name} for name in data["agents"].get(project(), [])]
elif args[:1] == ["start"]:
    data["agents"].setdefault(project(), []).append(args[1])
state.write_text(json.dumps(data))
print(text if text is not None else json.dumps(out))
'''


@pytest.fixture
def installed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SHELL", "/bin/unsupported")
    monkeypatch.setenv("SCION_HOME", str(home / ".scion"))
    state = tmp_path / "fake.json"
    state.write_text(json.dumps({"projects": {"alpha":"a", "beta":"b", "gamma":"c"},
                                "policies": {"a":"none", "b":"none", "c":"any"},
                                "switch":False, "agents":{}, "calls":[]}))
    monkeypatch.setenv("FAKE_HUB", str(state))
    source = tmp_path / "upstream"
    source.write_text(f"#!{sys.executable}\n" + FAKE)
    target = tmp_path / "bin/scion"
    install_scion(source, target)
    return target, state, home


def test_shell_cross_project_setup_apply_disable_preserves_owners(installed, tmp_path):
    target, state, home = installed
    def run(*args):
        return subprocess.run([target, "cross-project", *args], cwd=tmp_path,
                              capture_output=True, text=True, check=True)
    run("setup", "--project", "alpha", "--project", "beta", "--start")
    data = json.loads(state.read_text())
    assert data["switch"]
    assert data["policies"] == {"a":"members", "b":"members", "c":"any"}
    assert data["agents"] == {"a":["coordinator"], "b":["coordinator"]}
    config = home / ".scion/asf-cross-project-config.json"
    assert json.loads(config.read_text())["peers"] == {"alpha":"coordinator", "beta":"coordinator"}
    run("apply", "--start")
    assert json.loads(state.read_text())["agents"] == data["agents"]
    assert "alpha: members" in run("status").stdout
    run("disable")
    data = json.loads(state.read_text())
    assert data["policies"] == {"a":"none", "b":"none", "c":"any"}
    assert data["switch"]  # gamma still depends on it
    assert json.loads((home / ".scion/asf-cross-project.json").read_text())["projects"] == []


def test_shell_cross_project_invalid_selection_does_not_mutate_hub(installed):
    target, state, home = installed
    result = subprocess.run([target, "cross-project", "setup", "--project", "alpha", "--project", "missing"],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "unknown projects" in result.stderr
    assert not (home / ".scion/asf-cross-project-config.json").exists()
    assert not json.loads(state.read_text())["switch"]


def test_missing_peer_variable_is_successful_idempotent_cleanup(installed, monkeypatch):
    target, _, _ = installed
    monkeypatch.setenv("FAKE_CLEAR_ERROR", "Error: failed to delete environment variable: not_found: Resource not found (status: 404)")
    for args in (["setup", "--project", "alpha", "--project", "beta"], ["disable"], ["disable"]):
        result = subprocess.run([target, "cross-project", *args], capture_output=True, text=True, check=True)
        assert "Cross-project communication applied" in result.stdout
        assert "not_found" not in result.stderr


def test_peer_cleanup_reports_other_failures(installed, monkeypatch):
    target, _, _ = installed
    monkeypatch.setenv("FAKE_CLEAR_ERROR", "Error: permission_denied: cannot delete environment variable")
    result = subprocess.run([target, "cross-project", "setup", "--project", "alpha", "--project", "beta"],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "permission_denied" in result.stderr
    assert "Cross-project communication applied" not in result.stdout


@pytest.mark.parametrize("answer,expected", [
    ("1 3\nn\n", ["alpha", "gamma"]),
    ("1,2\nn\n", ["alpha", "beta"]),
    ("-1\nn\n", ["alpha", "beta", "gamma"]),
    ("99\nwrong\n1\n2 3\nn\n", ["beta", "gamma"]),
    ("none\n", []),
    ("except 2\nn\n", ["alpha", "gamma"]),
    ("-1 except 3\nn\n", ["alpha", "beta"]),
    ("prefix:a prefix:b\nn\n", ["alpha", "beta"]),
    ("prefix:a prefix:b prefix:g except 2\nn\n", ["alpha", "gamma"]),
    ("prefix:missing\n-1 except\nexcept 99\n-1 except 1 2 3\n-1 except 1\nn\n", ["beta", "gamma"]),
])
def test_shell_cross_project_numbered_selection(installed, answer, expected):
    target, _, home = installed
    result = subprocess.run([target, "cross-project", "setup"], input=answer,
                            capture_output=True, text=True, check=True)
    assert "1. alpha" in result.stdout
    assert "2. beta" in result.stdout
    assert "3. gamma" in result.stdout
    config = json.loads((home / ".scion/asf-cross-project-config.json").read_text())
    assert sorted(config["peers"]) == expected


def test_numbered_selection_enter_preserves_saved_peers(installed):
    target, _, home = installed
    config = home / ".scion/asf-cross-project-config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"peers":{"alpha":"lead", "gamma":"coordinator"}, "inbound":"members"}))
    result = subprocess.run([target, "cross-project", "setup"], input="\nn\n",
                            capture_output=True, text=True, check=True)
    assert "1. alpha [selected]" in result.stdout
    assert "[1 3]" in result.stdout
    assert json.loads(config.read_text())["peers"]["alpha"] == "lead"


def test_shell_local_initializes_and_uses_existing_state(installed, tmp_path):
    target, state, home = installed
    folder = tmp_path / "local folder"
    folder.mkdir()
    subprocess.run([target, "local", "init"], cwd=folder, capture_output=True, text=True, check=True)
    saved = list((home / ".config/scion-local/projects").glob("*.json"))
    assert len(saved) == 1
    assert json.loads(saved[0].read_text()) == {"id":"new-id", "slug":"local-folder-local", "folder":str(folder)}
    subprocess.run([target, "local", "start", "worker", "task with spaces"], cwd=folder,
                   capture_output=True, text=True, check=True)
    assert json.loads(state.read_text())["calls"][-1] == ["--project", "new-id", "start", "--workspace", str(folder), "worker", "task with spaces"]
    result = subprocess.run([target, "local", "start", "worker", "--project=other"], cwd=folder,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "overrides" in result.stderr
