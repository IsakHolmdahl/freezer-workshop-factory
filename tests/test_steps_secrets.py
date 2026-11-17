"""Tests for the GitHub token step. GitHub, Hub, prompts and legacy files are faked.

The autouse guard from test_steps_common is repeated here: any real process fails the test.
"""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

from steps import secrets, ui  # noqa: E402
from steps.common import Context  # noqa: E402
from test_steps_fakes import FakeAdapter, Result


@pytest.fixture(autouse=True)
def no_live_side_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError(f"test attempted a subprocess: {args[0] if args else kwargs}")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    monkeypatch.setattr(os, "system", forbidden)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Secrets live in tmp_path; the GitHub token is unset unless a test sets it."""
    secrets_file = tmp_path / ".env.secrets"
    secrets_file.write_text("GITHUB_TOKEN=\n")
    monkeypatch.setattr(secrets.fe, "SECRETS_FILE", secrets_file)
    monkeypatch.setattr(secrets.fe, "settings", lambda: _read(secrets_file))
    monkeypatch.setattr(ui, "ASSUME_YES", False)
    monkeypatch.setattr(secrets.fe, "get", lambda *a, **k: [])
    monkeypatch.setattr(secrets.fe, "hub_url", lambda: "https://hub.example")
    monkeypatch.setattr(secrets.fe, "set_local", lambda *a: None)
    monkeypatch.setattr(ui, "ask", lambda *a: "owner/repo")
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(secrets.credential_store, "backend", lambda: None)
    return secrets_file


def _read(path):
    from dotenv import dotenv_values
    return dict(dotenv_values(path))


def ctx(dry_run=False):
    lines = []
    adapter = FakeAdapter()
    original = adapter.scion

    def scion(args, **kwargs):
        result = original(args, **kwargs)
        result.stdout = '{"envVars": []}'
        return result

    adapter.scion = scion
    return Context(adapter=adapter, cfg={}, dry_run=dry_run, log=lines.append), lines


def fake_github(monkeypatch, status=200, login="alice"):
    def get(url, timeout, headers):
        return SimpleNamespace(ok=status == 200, status_code=status, headers={"X-OAuth-Scopes": "repo"},
                               json=lambda: {"login": login})

    monkeypatch.setattr(secrets.requests, "get", get)


def paste_lines(monkeypatch, *answers):
    """Feed input() lines; the guide's paste prompt reads them."""
    feed = iter(answers)
    monkeypatch.setattr(secrets.getpass, "getpass", lambda prompt="": next(feed))


TOKEN = "ghp_abcdefghijklmnop"


def test_valid_token_is_published_without_local_persistence(env, monkeypatch, capsys):
    fake_github(monkeypatch)
    paste_lines(monkeypatch, TOKEN, "")
    context, _ = ctx()
    result = secrets.run(context)
    assert result.status == "done"
    assert "GITHUB_TOKEN" not in _read(env)
    assert context.adapter.commands[-1] == [*secrets.SCION_FLAGS, "hub", "env", "set", "--scope", "hub",
                                           "--always", "--secret", "GITHUB_TOKEN", TOKEN]


def test_pat_can_be_deferred_without_token_prompt_or_network(env, monkeypatch, capsys):
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: False)

    def forbidden(*args, **kwargs):
        pytest.fail("deferring PAT setup must not prompt for a token or call GitHub")

    monkeypatch.setattr(secrets.getpass, "getpass", forbidden)
    monkeypatch.setattr(secrets.requests, "get", forbidden)
    before = env.read_text()
    result = secrets.run(ctx()[0])
    assert result.status == "skipped"
    assert "continuing setup" in result.message
    assert env.read_text() == before
    assert "make setup-github" in capsys.readouterr().out


def test_legacy_token_is_migrated_without_prompting(env, monkeypatch):
    fake_github(monkeypatch)
    env.write_text(f"GITHUB_TOKEN={TOKEN}\n")
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("must not prompt"))
    assert secrets.run(ctx()[0]).status == "done"
    assert "GITHUB_TOKEN" not in _read(env)


def test_hub_token_is_skipped_without_reading_credential(env, monkeypatch):
    context, _ = ctx()
    monkeypatch.setattr(context.adapter, "scion", lambda *a, **k:
                        Result(returncode=0, stdout='{"envVars": [{"key": "GITHUB_TOKEN", "sensitive": true}]}'))
    monkeypatch.setattr(secrets.requests, "get", lambda *a, **k: pytest.fail("must not fetch token"))
    monkeypatch.setattr(secrets.getpass, "getpass", lambda *a: pytest.fail("must not prompt"))
    assert secrets.run(context).status == "skipped"


def test_rejected_token_is_not_saved(env, monkeypatch):
    fake_github(monkeypatch, status=401)
    paste_lines(monkeypatch, TOKEN, "s")
    result = secrets.run(ctx()[0])
    assert result.status == "skipped"
    assert "GITHUB_TOKEN" in _read(env) and _read(env)["GITHUB_TOKEN"] == ""


def test_non_token_text_is_rejected_before_calling_github(env, monkeypatch):
    monkeypatch.setattr(secrets.requests, "get", lambda *a, **k: pytest.fail("no network for bad input"))
    assert secrets.check_token("not-a-token") == (
        False, "that does not look like a GitHub token (starts with gho_, ghp_ or github_pat_)")


def test_network_error_is_reported_not_raised(env, monkeypatch):
    def broken(*args, **kwargs):
        raise secrets.requests.ConnectionError("no route")

    monkeypatch.setattr(secrets.requests, "get", broken)
    ok, message = secrets.check_token(TOKEN)
    assert not ok and "could not reach GitHub" in message


def test_dry_run_prompts_nothing_and_writes_nothing(env, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("dry-run must not prompt"))
    before = env.read_text()
    result = secrets.run(ctx(dry_run=True)[0])
    assert result.status == "dry-run"
    assert env.read_text() == before


def test_yes_skips_token_guide_without_prompting_or_writing(env, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("--yes must not prompt"))
    context, _ = ctx()
    context.assume_yes = True
    before = env.read_text()
    result = secrets.run(context)
    assert result.status == "skipped"
    assert "--yes" in result.message
    assert env.read_text() == before


def test_quit_at_prompt_is_a_clean_skip(env, monkeypatch):
    paste_lines(monkeypatch, "q")
    result = secrets.run(ctx()[0])
    assert result.status == "skipped"
    assert "skipped" in result.message


def test_empty_pat_input_retries_instead_of_skipping(env, monkeypatch, capsys):
    fake_github(monkeypatch)
    paste_lines(monkeypatch, "", "   ", TOKEN)
    context, _ = ctx()
    assert secrets.run(context).status == "done"
    assert context.adapter.commands[-1][-1] == TOKEN
    output = capsys.readouterr().out
    assert "No PAT received" in output
    assert TOKEN not in output


@pytest.mark.parametrize("answer", ["s", "skip", "q"])
def test_pat_skip_requires_explicit_answer(env, monkeypatch, answer):
    paste_lines(monkeypatch, "", answer)
    monkeypatch.setattr(secrets.requests, "get", lambda *a, **k: pytest.fail("must not validate empty input"))
    context, _ = ctx()
    assert secrets.run(context).status == "skipped"
    assert not any("set" in command for command in context.adapter.commands)


def test_pat_eof_reports_input_problem_instead_of_skip(env, monkeypatch):
    def eof(*args, **kwargs):
        raise EOFError()

    monkeypatch.setattr(secrets.getpass, "getpass", eof)
    with pytest.raises(secrets.StepError, match="No input available"):
        secrets.run(ctx()[0])


def test_fine_grained_pat_checks_each_repository_and_rejects_missing_permissions(env, monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        denied = "/pulls" in url
        return SimpleNamespace(ok=not denied, status_code=403 if denied else 200,
                               headers={}, json=lambda: {"login": "alice", "permissions": {"push": True}})

    monkeypatch.setattr(secrets.requests, "get", get)
    ok, message = secrets.check_token("github_pat_example", ["owner/repo"])
    assert not ok and "Pull requests" in message and "403" in message
    assert any("/contents" in url for url in calls)


def test_classic_pat_without_repo_scope_is_rejected(env, monkeypatch):
    monkeypatch.setattr(secrets.requests, "get", lambda *a, **k:
                        SimpleNamespace(ok=True, headers={}, json=lambda: {"login": "alice"}))
    ok, message = secrets.check_token(TOKEN)
    assert not ok and "repo scope" in message


def test_existing_invalid_token_stops_setup(env, monkeypatch):
    from steps.common import StepError

    env.write_text(f"GITHUB_TOKEN={TOKEN}\n")
    fake_github(monkeypatch, status=401)
    with pytest.raises(StepError, match="validation failed"):
        secrets.run(ctx()[0])


def test_replace_validates_and_saves_repository_selection(env, monkeypatch):
    env.write_text(f"GITHUB_TOKEN={TOKEN}\n")
    fake_github(monkeypatch)
    paste_lines(monkeypatch, "github_pat_replacement")
    saved = []
    monkeypatch.setattr(secrets.fe, "set_local", lambda *args: saved.append(args))
    secrets.run(ctx()[0], replace=True)
    assert "GITHUB_TOKEN" not in _read(env)
    assert saved == [("github.repositories", ["owner/repo"])]


def test_publication_failure_preserves_legacy_token_and_hides_credentials(env, monkeypatch):
    from steps.common import StepError

    env.write_text(f"GITHUB_TOKEN={TOKEN}\n")
    fake_github(monkeypatch)
    context, _ = ctx()
    monkeypatch.setattr(context.adapter, "scion", lambda *a, **k:
                        Result(returncode=1, stderr=TOKEN))
    paste_lines(monkeypatch, TOKEN)
    with pytest.raises(StepError) as error:
        secrets.run(context, replace=True)
    assert TOKEN not in str(error.value)
    assert _read(env)["GITHUB_TOKEN"] == TOKEN


def test_repository_without_push_access_is_rejected(env, monkeypatch):
    monkeypatch.setattr(secrets.requests, "get", lambda *a, **k:
                        SimpleNamespace(ok=True, headers={},
                                        json=lambda: {"login": "alice", "permissions": {"push": False}}))
    ok, message = secrets.check_token("github_pat_example", ["owner/repo"])
    assert not ok and "push access" in message


def test_empty_repository_is_validated_without_requiring_contents(env, monkeypatch):
    def get(url, **kwargs):
        empty = "matching-refs" in url
        no_contents = url.endswith("/contents")
        return SimpleNamespace(ok=not no_contents, status_code=404 if no_contents else 200,
                               headers={}, json=lambda: [] if empty else {"login": "alice"})

    monkeypatch.setattr(secrets.requests, "get", get)
    ok, message = secrets.check_token("github_pat_example", ["owner/empty"])
    assert ok and "checked 1" in message


def test_failure_does_not_save_token_and_retry_can_be_skipped(env, monkeypatch):
    calls = []

    def get(url, **kwargs):
        denied = "/contents" in url
        calls.append(url)
        return SimpleNamespace(ok=not denied, status_code=403 if denied else 200,
                               headers={}, json=lambda: {"login": "alice"})

    monkeypatch.setattr(secrets.requests, "get", get)
    paste_lines(monkeypatch, "github_pat_example", "s")
    assert secrets.run(ctx()[0]).status == "skipped"
    assert _read(env)["GITHUB_TOKEN"] == ""


def test_cli_help_exits_zero():
    with pytest.raises(SystemExit) as exit_info:
        secrets.main(["--help"])
    assert exit_info.value.code == 0


def test_keyring_pat_is_validated_and_republished_without_prompt(env, monkeypatch):
    fake_github(monkeypatch)
    seen = []
    store = SimpleNamespace(get_password=lambda *args: seen.append(args) or TOKEN)
    monkeypatch.setattr(secrets.credential_store, "backend", lambda: store)
    monkeypatch.setattr(secrets.fe, "hub_url", lambda: "https://hub.example/")
    monkeypatch.setattr(secrets.getpass, "getpass", lambda *a: pytest.fail("must reuse stored PAT"))
    context, _ = ctx()
    context.assume_yes = True
    assert secrets.run(context, keyring=True).status == "done"
    assert seen == [("asf-agent-github", "https://hub.example")]
    assert context.adapter.commands[-1][-1] == TOKEN
    assert TOKEN not in env.read_text()


def test_rotation_saves_validated_pat_to_keyring(env, monkeypatch):
    fake_github(monkeypatch)
    paste_lines(monkeypatch, TOKEN)
    saved = []
    store = SimpleNamespace(set_password=lambda *args: saved.append(args))
    monkeypatch.setattr(secrets.credential_store, "backend", lambda: store)
    monkeypatch.setattr(secrets.fe, "hub_url", lambda: "https://hub.example")
    assert secrets.run(ctx()[0], replace=True, keyring=True).status == "done"
    assert saved == [("asf-agent-github", "https://hub.example", TOKEN)]
    assert TOKEN not in env.read_text()


def test_explicit_keyring_requires_secure_backend(env):
    from steps.common import StepError

    with pytest.raises(StepError, match="No supported secure desktop keyring"):
        secrets.run(ctx()[0], keyring=True)
