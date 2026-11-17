"""Discover existing Scion installations, choose a destination and clean duplicates."""
import os
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from steps import build, scion_cli, ui
from steps.common import Context, StepError
from test_steps_fakes import no_live_side_effects  # noqa: F401 (autouse guard)


@pytest.fixture
def env(monkeypatch, tmp_path):
    home = tmp_path / "home"
    paths = [home / ".local/bin/scion", home / ".config/scion/scion",
             tmp_path / "usr/local/bin/scion"]
    config = {"path": paths[0]}
    saved = []
    monkeypatch.setattr(scion_cli, "common_paths", lambda: paths)
    monkeypatch.setattr(scion_cli.fe, "install_path", lambda cfg=None: config["path"])

    def save(key, value):
        saved.append((key, value))
        config["path"] = Path(value)

    monkeypatch.setattr(scion_cli.fe, "set_local", save)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(ui, "choose", lambda prompt, choices: next(iter(choices)))
    monkeypatch.setattr(ui, "confirm", lambda prompt, default: False)
    return SimpleNamespace(paths=paths, saved=saved, config=config, monkeypatch=monkeypatch)


def binary(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def ctx(**options):
    return Context(adapter=None, cfg={}, **options)


def test_common_locations(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert scion_cli.common_paths() == [tmp_path / ".local/bin/scion",
                                       tmp_path / ".config/scion/scion",
                                       Path("/usr/local/bin/scion")]


def test_fresh_install_asks_with_default_without_needing_a_binary(env, capsys):
    seen = []
    env.monkeypatch.setattr(ui, "choose", lambda prompt, choices: seen.append(choices) or next(iter(choices)))
    assert scion_cli.run(ctx()).status == "done"
    assert list(seen[0]) == list(map(str, env.paths))
    assert not env.paths[0].exists()
    assert env.saved == []
    assert os.environ["PATH"].startswith(str(env.paths[0].parent))
    assert "No Scion binary found" in capsys.readouterr().out


@pytest.mark.parametrize("index", [0, 1, 2])
def test_existing_common_location_is_recommended(env, index):
    existing = binary(env.paths[index])
    assert scion_cli.discover() == [existing]
    scion_cli.run(ctx())
    assert env.config["path"] == existing


def test_path_binary_is_recommended_over_common_locations_and_config(env, tmp_path):
    active = binary(tmp_path / "custom bin/scion")
    other = binary(tmp_path / "second/scion")
    for path in env.paths:
        binary(path)
    env.monkeypatch.setenv("PATH", f"{active.parent}:{other.parent}:{active.parent}")
    assert scion_cli.discover() == [active, other, *env.paths]
    scion_cli.run(ctx())
    assert env.saved == [("scion.install_path", str(active))]


def test_user_can_choose_another_location_and_build_uses_it(env):
    binary(env.paths[1])
    selected = env.paths[2]
    env.monkeypatch.setattr(ui, "choose", lambda prompt, choices: str(selected))
    calls = []
    env.monkeypatch.setattr(build, "_run", lambda argv, env: calls.append(argv))
    scion_cli.run(ctx())
    build.run_scion(ctx())
    assert calls[0][-2:] == ["--install-path", str(selected)]


@pytest.mark.parametrize("remove", [False, True])
def test_duplicate_removal_is_optional_and_keeps_selected_binary(env, remove):
    for path in env.paths:
        binary(path)
    prompts = []
    env.monkeypatch.setattr(ui, "confirm", lambda prompt, default: prompts.append(default) or remove)
    scion_cli.run(ctx())
    assert prompts == [False]
    assert env.paths[0].exists()
    assert all(path.exists() != remove for path in env.paths[1:])


def test_yes_uses_recommendation_without_prompts_or_deleting_duplicates(env):
    for path in env.paths:
        binary(path)

    def forbidden(*args, **kwargs):
        raise AssertionError("--yes must not prompt")

    env.monkeypatch.setattr(ui, "choose", forbidden)
    env.monkeypatch.setattr(ui, "confirm", forbidden)
    scion_cli.run(ctx(assume_yes=True))
    assert all(path.exists() for path in env.paths)


def test_dry_run_only_discovers(env):
    binary(env.paths[1])
    path = os.environ["PATH"]

    def forbidden(*args, **kwargs):
        raise AssertionError("dry-run must not prompt")

    env.monkeypatch.setattr(ui, "choose", forbidden)
    assert scion_cli.run(ctx(dry_run=True)).status == "dry-run"
    assert os.environ["PATH"] == path
    assert env.saved == []
    assert env.paths[1].exists()


def test_symlink_to_selected_binary_is_kept(env):
    binary(env.paths[0])
    env.paths[1].parent.mkdir(parents=True)
    env.paths[1].symlink_to(env.paths[0])
    env.monkeypatch.setattr(ui, "confirm", lambda prompt, default: True)
    scion_cli.run(ctx())
    assert env.paths[0].exists()
    assert env.paths[1].is_symlink()


def test_duplicate_permission_error_has_recovery_instructions(env):
    for path in env.paths[:2]:
        binary(path)
    env.monkeypatch.setattr(ui, "confirm", lambda prompt, default: True)

    def denied(self):
        raise PermissionError("permission denied")

    env.monkeypatch.setattr(Path, "unlink", denied)
    with pytest.raises(StepError) as error:
        scion_cli.run(ctx())
    assert str(env.paths[1]) in error.value.message
    assert "sudo rm" in error.value.fix[0]


def test_nonexecutable_files_are_not_recommended(env):
    env.paths[1].parent.mkdir(parents=True)
    env.paths[1].write_text("not executable")
    assert scion_cli.discover() == []


def test_path_command_is_shell_quoted(env, tmp_path, capsys):
    selected = tmp_path / "local bin/scion"
    env.config["path"] = selected
    scion_cli.run(ctx(assume_yes=True))
    assert f'export PATH={shlex.quote(str(selected.parent))}:"$PATH"' in capsys.readouterr().out


def test_cli_help_exits_zero():
    with pytest.raises(SystemExit) as exit_info:
        scion_cli.main(["--help"])
    assert exit_info.value.code == 0
