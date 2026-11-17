"""Tests for cross-project.py with a fake `scion` CLI."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
import factory_env as fe  # noqa: E402

spec = importlib.util.spec_from_file_location("cross_project", SCRIPTS / "cross-project.py")
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)


class FakeHub:
    def __init__(self):
        self.switch, self.hub_rev = False, 0
        self.projects = {"alpha": "id-a", "beta": "id-b", "gamma": "id-c", "global": "id-g"}
        self.policy = {pid: ["none", 1] for pid in self.projects.values()}
        self.env, self.started, self.agents = {}, [], {"id-a": ["coordinator"]}

    def __call__(self, argv, **kwargs):
        args = argv[2:]  # strip "scion --non-interactive"
        out = ""
        if args[:2] == ["hub", "projects"]:
            out = json.dumps([{"slug": s, "id": i} for s, i in self.projects.items()])
        elif args[:3] == ["hub", "messaging", "get"]:
            out = f"Hub Messaging Settings (revision {self.hub_rev}):\n  cross_project_messaging_enabled: {str(self.switch).lower()}\n"
        elif args[:3] == ["hub", "messaging", "set"]:
            assert args[args.index("--revision") + 1] == str(self.hub_rev)
            self.switch, self.hub_rev = args[3].endswith("true"), self.hub_rev + 1
        elif args[:3] == ["project", "messaging", "get"]:
            policy, rev = self.policy[args[4]]
            out = f"Project Messaging Policy (revision {rev}):\n  cross_project_inbound:           {policy}\n"
        elif args[:3] == ["project", "messaging", "set"]:
            pid = args[4]
            assert args[args.index("--revision") + 1] == str(self.policy[pid][1])
            self.policy[pid] = [args[args.index("--policy") + 1], self.policy[pid][1] + 1]
        elif args[:3] == ["hub", "env", "set"]:
            self.env[(self._scope(args), args[-2])] = args[-1]
        elif args[:3] == ["hub", "env", "clear"]:
            self.env.pop((self._scope(args), args[-1]), None)
        elif args[:1] == ["list"]:
            out = json.dumps([{"slug": n, "phase": "running"} for n in self.agents.get(args[2], [])])
        elif args[:1] == ["start"]:
            self.started.append((args[1], args[3]))
        return subprocess.CompletedProcess(argv, 0, out, "")

    @staticmethod
    def _scope(args):
        return next((a.split("=", 1)[1] for a in args if a.startswith("--project=")), "hub")


@pytest.fixture
def hub(tmp_path, monkeypatch):
    (tmp_path / "asf.yaml").write_text((fe.ROOT / "asf.yaml").read_text())
    monkeypatch.setattr(fe, "CONFIG_FILE", tmp_path / "asf.yaml")
    monkeypatch.setattr(fe, "LOCAL_CONFIG_FILE", tmp_path / "asf.local.yaml")
    monkeypatch.setattr(cp, "STATE_FILE", tmp_path / "state.json")
    fake = FakeHub()
    monkeypatch.setattr(fe, "run", fake)
    return tmp_path, fake


def test_apply_opens_selected_and_leaves_owners_projects(hub):
    root, fake = hub
    (root / "asf.local.yaml").write_text(
        "cross_project:\n  enabled: true\n  projects:\n    alpha: coordinator\n    beta: lead\n")
    fake.policy["id-c"] = ["any", 4]  # gamma's owner opened it: not ours to change
    fake.env[("hub", "ASF_PEER_PROJECTS")] = "old"  # Hub-wide value of earlier versions
    assert cp.main(["apply", "--start"]) == 0
    assert fake.switch is True
    assert [fake.policy[i][0] for i in ("id-a", "id-b", "id-c")] == ["members", "members", "any"]
    assert fake.env == {("id-a", "ASF_PEER_PROJECTS"): "alpha=coordinator,beta=lead",
                        ("id-b", "ASF_PEER_PROJECTS"): "alpha=coordinator,beta=lead"}
    assert fake.started == [("lead", "id-b")]  # alpha already has its coordinator
    assert cp.managed() == {"alpha", "beta"}

    # deselect beta: only what we opened is closed again
    (root / "asf.local.yaml").write_text(
        "cross_project:\n  enabled: true\n  projects:\n    alpha: coordinator\n    gamma: coordinator\n")
    assert cp.main(["apply"]) == 0
    assert [fake.policy[i][0] for i in ("id-a", "id-b", "id-c")] == ["members", "none", "members"]
    assert ("id-b", "ASF_PEER_PROJECTS") not in fake.env


def test_unknown_project_single_project_and_disable(hub, capsys):
    root, fake = hub
    (root / "asf.local.yaml").write_text("cross_project:\n  enabled: true\n  projects: [nope]\n")
    assert cp.main(["apply"]) == 1
    assert "unknown project(s)" in capsys.readouterr().err
    (root / "asf.local.yaml").write_text("cross_project:\n  enabled: true\n  projects: [alpha, beta]\n")
    assert cp.main(["apply"]) == 0
    fake.policy["id-c"] = ["any", 3]  # not ours
    assert cp.main(["disable"]) == 0
    assert [fake.policy[i][0] for i in ("id-a", "id-b", "id-c")] == ["none", "none", "any"]
    assert fake.switch is True  # gamma still relies on it
    fake.policy["id-c"] = ["none", 4]
    (root / "asf.local.yaml").write_text("cross_project:\n  enabled: true\n  projects: [alpha]\n")
    assert cp.main(["apply"]) == 0  # one project: nothing to talk to
    assert fake.switch is False and cp.managed() == set()


def test_setup_saves_selection_and_applies_with_receivers(hub):
    _, fake = hub
    assert cp.main(["setup", "--project", "alpha", "--project", "beta", "--start", "--inbound", "any"]) == 0
    assert cp.desired() == ({"alpha": "coordinator", "beta": "coordinator"}, "any")
    assert fake.switch
    assert fake.policy["id-a"][0] == fake.policy["id-b"][0] == "any"
    assert fake.started == [("coordinator", "id-b")]


def test_interactive_setup_preserves_receivers_and_can_clear_selection(hub, monkeypatch):
    root, fake = hub
    from steps import ui
    (root / "asf.local.yaml").write_text(
        "cross_project:\n  enabled: true\n  projects: {alpha: coordinator, beta: lead}\n")
    monkeypatch.setattr(cp.sys.stdin, "isatty", lambda: True)
    choices = []
    monkeypatch.setattr(ui, "checkbox", lambda prompt, options: choices.extend(options) or ["alpha", "beta"])
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: False)
    assert cp.main([]) == 0
    assert choices == [("alpha", "alpha", True), ("beta", "beta", True), ("gamma", "gamma", False)]
    assert cp.desired()[0]["beta"] == "lead"
    monkeypatch.setattr(ui, "checkbox", lambda *a: [])
    assert cp.main(["setup"]) == 0
    assert cp.desired()[0] == {}
    assert not fake.switch
    assert fake.policy["id-a"][0] == fake.policy["id-b"][0] == "none"


@pytest.mark.parametrize("projects", [["alpha"], ["alpha", "unknown"]])
def test_invalid_setup_selection_changes_nothing(hub, projects):
    root, fake = hub
    args = ["setup"]
    for slug in projects:
        args += ["--project", slug]
    assert cp.main(args) == 1
    assert not (root / "asf.local.yaml").exists()
    assert not fake.switch
    assert not cp.managed()
