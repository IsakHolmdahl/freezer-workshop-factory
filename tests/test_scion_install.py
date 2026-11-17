"""Exercise installed CLI dispatch with fake executables, without a running Hub."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scion_install import install_scion, is_shim, upstream_binary


@pytest.fixture(autouse=True)
def isolated_shell_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in ("ZDOTDIR", "XDG_CONFIG_HOME", "__fish_config_dir"):
        monkeypatch.delenv(name, raising=False)


def executable(path, body):
    path.write_text(f"#!{sys.executable}\n{body}\n")
    path.chmod(0o755)
    return path


def test_install_and_rebuild_forward_arguments_cwd_and_exit_status(tmp_path):
    source = executable(tmp_path / "built-scion", "import json, os, sys\n"
                        "print(json.dumps([sys.argv[1:], os.getcwd()]))\nsys.exit(23)")
    target = tmp_path / "bin with spaces/scion"
    install_scion(source, target)
    assert is_shim(target)
    assert target.read_text().startswith("#!/bin/sh\n")
    assert upstream_binary(target).read_bytes() == source.read_bytes()
    args = ["start", "agent", "task with spaces", "", "--type=developer"]
    result = subprocess.run([target, *args], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 23
    assert json.loads(result.stdout) == [args, str(tmp_path)]
    source.write_text(f"#!{sys.executable}\nprint('rebuilt')\n")
    install_scion(source, target)
    assert subprocess.check_output([target, "version"], text=True).strip() == "rebuilt"


def test_local_helper_can_call_scion_without_recursion(tmp_path):
    source = executable(tmp_path / "upstream", "import json, sys\nprint(json.dumps(sys.argv[1:]))")
    target = tmp_path / "bin/scion"
    install_scion(source, target)
    Path.home().mkdir(parents=True, exist_ok=True)
    saved = Path.home() / ".config/scion-local/projects" / (hashlib.sha256(str(tmp_path).encode()).hexdigest() + ".json")
    saved.parent.mkdir(parents=True)
    saved.write_text('{"id":"local-id"}')
    result = subprocess.run([target, "local", "start", "agent", "task with spaces"],
                            cwd=tmp_path, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == ["--project", "local-id", "start", "--workspace", str(tmp_path), "agent", "task with spaces"]


def test_installed_cross_project_help_works_outside_checkout(tmp_path):
    source = executable(tmp_path / "upstream", "raise RuntimeError('must use extension')")
    target = tmp_path / "bin/scion"
    install_scion(source, target)
    result = subprocess.run([target, "cross-project", "--help"], cwd=tmp_path,
                            capture_output=True, text=True, check=True)
    assert "scion cross-project" in result.stdout
    assert "--project" in result.stdout
    assert "setup" in result.stdout


def test_wrap_existing_binary_in_place_and_repeat(tmp_path):
    target = executable(tmp_path / "scion", "print('upstream')")
    original = target.read_bytes()
    install_scion(target, target)
    install_scion(target, target)
    assert is_shim(target)
    assert upstream_binary(target).read_bytes() == original


def test_shell_shim_resolves_path_and_relative_symlink_alias(tmp_path):
    source = executable(tmp_path / "upstream", "import json, sys\nprint(json.dumps(sys.argv[1:]))")
    target = tmp_path / "bin with 'quotes/scion"
    install_scion(source, target)
    alias = tmp_path / "alias"
    alias.symlink_to(target.relative_to(tmp_path))
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}"}
    result = subprocess.run(["alias", "version", ""], env=env, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == ["version", ""]


@pytest.mark.parametrize("protocol", ["__complete", "__completeNoDesc"])
def test_shell_shim_adds_extensions_to_upstream_completion(tmp_path, protocol):
    source = executable(tmp_path / "upstream", "print('list\\tList agents\\n:4')")
    target = tmp_path / "bin/scion"
    install_scion(source, target)
    def complete(*words):
        lines = subprocess.check_output([target, protocol, *words], text=True).splitlines()
        for line in lines:
            if line.startswith(("list\t", ":")):
                continue
            assert ("\t" in line) == (protocol == "__complete")
        return [line if line.startswith("list\t") else line.split("\t")[0] for line in lines]
    assert complete("") == ["list\tList agents", "local", "cross-project", "sessions", "watch", ":4"]
    assert complete("cr") == ["list\tList agents", "cross-project", ":4"]
    assert complete("cross-project", "s") == ["setup", "status", ":4"]
    assert complete("cross-project", "setup", "--st") == ["--start", ":4"]
    assert complete("cross-project", "setup", "--inbound", "") == ["members", "any", ":4"]
    assert complete("local", "in") == ["init", ":4"]
    assert complete("local", "init", "--b") == ["--broker", ":4"]
    assert complete("local", "start", "") == ["list\tList agents", ":4"]
    assert complete("watch", "--panes", "") == ["1", "2", "3", "4", "5", "6", "7", "8", ":4"]


def test_missing_helper_reports_actionable_error(tmp_path):
    source = executable(tmp_path / "upstream", "pass")
    target = tmp_path / "bin/scion"
    install_scion(source, target)
    target.with_name("scion-local.sh").unlink()
    env = {**os.environ, "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    result = subprocess.run([target, "local", "--help"], env=env,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "scion-local" in result.stderr


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_completion_detects_shell_and_installs_activation(tmp_path, monkeypatch, capsys, shell):
    monkeypatch.setenv("SHELL", f"/usr/bin/{shell}")
    source = executable(tmp_path / "upstream", "import json, sys\nprint(json.dumps(sys.argv[1:]))")
    target = tmp_path / "bin with spaces/scion"
    install_scion(source, target)
    config = Path.home() / {"bash": ".bashrc", "zsh": ".zshrc", "fish": ".config/fish/config.fish"}[shell]
    activation = config.read_text()
    assert str(config) in capsys.readouterr().out
    assert f"'{target}' completion {shell}" in activation
    assert ("| source" if shell == "fish" else "source <(") in activation
    if shell == "zsh":
        assert "compinit" in activation
    result = subprocess.run([target, "completion"], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == ["completion", shell]
    # Explicit selection and completion protocol requests pass through unchanged.
    for args in (["completion", "bash", "--no-descriptions"],
                 ["completion", "--help"], ["__complete", "start", ""]):
        result = subprocess.run([target, *args], capture_output=True, text=True, check=True)
        assert json.loads(result.stdout) == args


@pytest.mark.parametrize("shell", ["", "/bin/tcsh"])
def test_completion_requires_explicit_shell_when_detection_fails(tmp_path, monkeypatch, shell):
    monkeypatch.setenv("SHELL", shell)
    source = executable(tmp_path / "upstream", "print('explicit completion')")
    target = tmp_path / "bin/scion"
    install_scion(source, target)
    result = subprocess.run([target, "completion"], capture_output=True, text=True)
    assert result.returncode == 2
    assert "scion completion bash|zsh|fish" in result.stderr
    assert not result.stdout
    assert subprocess.check_output([target, "completion", "fish"], text=True).strip() == "explicit completion"


@pytest.mark.parametrize("shell,env,relative", [
    ("bash", None, ".bashrc"),
    ("zsh", "ZDOTDIR", ".zshrc"),
    ("fish", "XDG_CONFIG_HOME", "fish/config.fish"),
    ("fish", "__fish_config_dir", "config.fish"),
])
def test_completion_preserves_config_and_updates_one_block(tmp_path, monkeypatch, shell, env, relative):
    monkeypatch.setenv("SHELL", f"/bin/{shell}")
    root = tmp_path / "custom" if env else Path.home()
    if env:
        monkeypatch.setenv(env, str(root))
    config = root / relative
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("# existing user configuration")
    source = executable(tmp_path / "upstream", "pass")
    target = tmp_path / "bin/scion"
    install_scion(source, target)
    first = config.read_text()
    install_scion(source, target)
    assert config.read_text() == first
    assert first.startswith("# existing user configuration\n")
    with config.open("a") as stream:
        stream.write("# later user configuration\n")
    new_target = tmp_path / "other/scion"
    install_scion(source, new_target)
    updated = config.read_text()
    assert updated.count("# >>> ASF Scion completion >>>") == 1
    assert updated.startswith("# existing user configuration\n")
    assert updated.endswith("# later user configuration\n")
    assert str(new_target) in updated
    assert str(target) not in updated
