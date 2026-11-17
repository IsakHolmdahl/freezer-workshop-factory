"""Relocated Scion state uses one path and preserves existing user data."""
import pytest

import factory_env as fe
import scion_settings


@pytest.fixture
def home(tmp_path, monkeypatch):
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.delenv("SCION_HOME", raising=False)
    monkeypatch.setattr(fe, "ENV_FILE", tmp_path / ".env")
    return user, tmp_path / "state"


def test_default_and_env_override(home, monkeypatch):
    user, target = home
    assert fe.scion_home() == user / ".scion"
    fe.ENV_FILE.write_text(f"SCION_HOME={target}\n")
    assert scion_settings.settings_path() == target / "settings.yaml"
    monkeypatch.setenv("SCION_HOME", "~/alternate")
    assert fe.scion_home() == user / "alternate"


def test_setup_links_custom_home_for_upstream_cli(home):
    user, target = home
    fe.ENV_FILE.write_text(f"SCION_HOME={target}\n")
    fe.ensure_scion_home()
    assert (user / ".scion").is_symlink()
    assert (user / ".scion").resolve() == target
    fe.ensure_scion_home()


def test_existing_home_is_never_overwritten(home):
    user, target = home
    (user / ".scion").mkdir()
    (user / ".scion/settings.yaml").write_text("user settings")
    fe.ENV_FILE.write_text(f"SCION_HOME={target}\n")
    with pytest.raises(ValueError, match="already exists"):
        fe.ensure_scion_home()
    assert (user / ".scion/settings.yaml").read_text() == "user settings"
    assert not target.exists()


def test_cleanup_removes_custom_state_and_compatibility_link(home, monkeypatch):
    import cleanup

    user, target = home
    fe.ENV_FILE.write_text(f"SCION_HOME={target}\n")
    fe.ensure_scion_home()
    monkeypatch.setattr(cleanup, "SCION_HOME", target)
    cleanup.remove_scion_home()
    assert not target.exists()
    assert not (user / ".scion").is_symlink()


def test_doctor_reports_missing_compatibility_link(home):
    import doctor

    _, target = home
    fe.ENV_FILE.write_text(f"SCION_HOME={target}\n")
    result, = doctor.check_scion_home_owner()
    assert result.status == doctor.FAIL
    assert "does not point there" in result.detail
