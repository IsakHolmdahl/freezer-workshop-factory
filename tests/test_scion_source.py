"""Exercise ref selection with a real, local Git repository; no network or builds."""
import subprocess

import pytest

import scion_source


@pytest.fixture
def source(tmp_path, monkeypatch):
    repo = tmp_path / "scion"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

    git("init", "-q")
    (repo / "Makefile").write_text("all:\n")
    git("add", "Makefile")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "release")
    git("tag", "v-test")
    release = git("rev-parse", "HEAD")
    (repo / "Makefile").write_text("all:\n\ttrue\n")
    git("add", "Makefile")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "development")
    monkeypatch.setattr(scion_source.fe, "SCION_SRC", repo)
    monkeypatch.setattr(scion_source, "source_ref", lambda: "v-test")
    return repo, git, release


def test_selects_tag_and_is_idempotent(source):
    _, git, release = source
    assert scion_source.ensure_source()
    assert git("rev-parse", "HEAD") == release
    assert not scion_source.ensure_source()


def test_preserves_local_changes(source):
    repo, git, _ = source
    before = git("rev-parse", "HEAD")
    (repo / "Makefile").write_text("user changes\n")
    with pytest.raises(RuntimeError, match="local changes"):
        scion_source.ensure_source()
    assert git("rev-parse", "HEAD") == before


def test_dry_run_does_not_change_checkout(source):
    _, git, _ = source
    before = git("rev-parse", "HEAD")
    assert not scion_source.ensure_source(dry_run=True)
    assert git("rev-parse", "HEAD") == before


def test_source_ref_reads_env_and_template_default(tmp_path, monkeypatch):
    template = tmp_path / ".env.template"
    template.write_text("SCION_SOURCE_REF=v-default\n")
    monkeypatch.setattr(scion_source.fe, "ENV_TEMPLATE", template)
    monkeypatch.setattr(scion_source.fe, "settings", lambda: {})
    assert scion_source.source_ref() == "v-default"
    monkeypatch.setattr(scion_source.fe, "settings", lambda: {"SCION_SOURCE_REF": "v-custom"})
    assert scion_source.source_ref() == "v-custom"
