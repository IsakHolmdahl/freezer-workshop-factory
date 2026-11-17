"""Exercise session viewer shell commands with fake Scion and tmux boundaries."""
import json
import os
import shlex
import subprocess
import sys

import pytest

from scion_install import install_scion


@pytest.fixture
def viewer(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SHELL", "/bin/unsupported")
    records = tmp_path / "tmux.jsonl"
    upstream = tmp_path / "upstream"
    agents = [{"slug":f"agent-{i}", "name":f"Agent {i}", "project":"demo",
               "projectId":"project-id", "phase":"running"} for i in range(9)]
    upstream.write_text(f"#!{sys.executable}\nimport json, sys\n"
                        f"assert sys.argv[1:4] == ['list', '--phase', 'running']\nprint({json.dumps(json.dumps(agents))})\n")
    target = tmp_path / "bin/scion"
    install_scion(upstream, target)
    tmux = target.with_name("tmux")
    tmux.write_text(f"#!{sys.executable}\nimport json, sys\n"
                    f"with open({str(records)!r}, 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                    "if sys.argv[1] == 'has-session': sys.exit(1)\n"
                    "if sys.argv[1] in ('new-session','new-window'): print('@1')\n")
    tmux.chmod(0o755)
    monkeypatch.setenv("PATH", f"{target.parent}:{os.environ['PATH']}")
    return target, records


@pytest.mark.parametrize("panes,windows,splits", [(4, 3, 6), (1, 9, 0), (8, 2, 7)])
def test_watch_groups_agents_into_panes_and_tabs(viewer, panes, windows, splits):
    target, records = viewer
    result = subprocess.run([target, "watch", "--all", "--panes", str(panes), "--session", "test-view", "--detach"],
                            capture_output=True, text=True, check=True)
    calls = [json.loads(line) for line in records.read_text().splitlines()]
    assert sum(c[0] == "new-session" for c in calls) == 1
    assert sum(c[0] == "new-window" for c in calls) == windows - 1
    assert sum(c[0] == "split-window" for c in calls) == splits
    launches = [c[-1] for c in calls if c[0] in ("new-session", "new-window", "split-window")]
    for index, launch in enumerate(launches):
        assert shlex.split(launch) == ["exec", str(target) + "-real", "--project", "project-id", "attach", f"agent-{index}"]
    assert "9 agents" in result.stdout
    assert not any(c[0] in ("attach-session", "switch-client", "kill-session") for c in calls)


def test_sessions_lists_candidates_and_watch_selects_names(viewer):
    target, records = viewer
    result = subprocess.run([target, "sessions", "--json"], capture_output=True, text=True, check=True)
    assert len(json.loads(result.stdout)) == 9
    assert not records.exists()
    subprocess.run([target, "watch", "agent-2", "agent-4", "--detach"], capture_output=True, text=True, check=True)
    calls = [json.loads(line) for line in records.read_text().splitlines()]
    assert sum(c[0] == "split-window" for c in calls) == 1


def test_invalid_or_missing_agents_never_create_tmux_session(viewer):
    target, records = viewer
    for args in (["--panes", "0"], ["missing-agent"], ["--all", "--project", "demo"]):
        result = subprocess.run([target, "watch", *args, "--detach"], capture_output=True, text=True)
        assert result.returncode != 0
    assert not records.exists()


def test_watch_cleans_up_only_new_viewer_on_partial_failure(viewer):
    target, records = viewer
    tmux = target.with_name("tmux")
    original = tmux.read_text()
    tmux.write_text(original + "\nif sys.argv[1] == 'split-window': sys.exit(1)\n")
    result = subprocess.run([target, "watch", "--session", "partial-view", "--detach"],
                            capture_output=True, text=True)
    assert result.returncode != 0
    calls = [json.loads(line) for line in records.read_text().splitlines()]
    assert ["kill-session", "-t", "partial-view"] in calls


def test_watch_never_replaces_existing_session(viewer):
    target, records = viewer
    tmux = target.with_name("tmux")
    tmux.write_text(tmux.read_text().replace("sys.exit(1)", "sys.exit(0)"))
    result = subprocess.run([target, "watch", "--session", "existing", "--detach"],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "already exists" in result.stderr
    calls = [json.loads(line) for line in records.read_text().splitlines()]
    assert [c[0] for c in calls] == ["has-session"]
