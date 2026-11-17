"""Doctor shares setup's binary discovery and never removes installations."""
import pytest

import doctor
from test_steps_fakes import no_live_side_effects  # noqa: F401


@pytest.fixture
def locations(monkeypatch, tmp_path):
    configured = tmp_path / "local bin/scion"
    other = tmp_path / "other/scion"
    for path in (configured, other):
        path.parent.mkdir()
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
    monkeypatch.setattr(doctor.fe, "install_path", lambda: configured)
    monkeypatch.setattr(doctor.scion_cli, "discover", lambda: [configured])
    monkeypatch.setattr(doctor.fe, "which", lambda name: str(configured))
    return configured, other


def test_single_aligned_installation_passes(locations):
    results = doctor.check_scion_binaries()
    assert all(result.status == doctor.OK for result in results)


def test_missing_binary_has_fresh_install_instructions(locations, monkeypatch):
    monkeypatch.setattr(doctor.scion_cli, "discover", lambda: [])
    result, = doctor.check_scion_binaries()
    assert result.status == doctor.FAIL
    assert result.fix == ["./setup.py --only scion_cli,build_scion"]


def test_duplicates_warn_without_automatic_deletion(locations, monkeypatch):
    configured, other = locations
    monkeypatch.setattr(doctor.scion_cli, "discover", lambda: [configured, other])
    results = doctor.check_scion_binaries()
    duplicate = next(result for result in results if "duplicate" in result.title)
    assert duplicate.status == doctor.WARN
    assert str(other) in duplicate.detail
    assert "./setup.py --only scion_cli" in duplicate.fix[0]
    assert doctor.apply_fixes([("Scion CLI", results)]) == 0
    assert configured.exists() and other.exists()


def test_symlink_alias_is_not_a_duplicate(locations, monkeypatch):
    configured, other = locations
    other.unlink()
    other.symlink_to(configured)
    monkeypatch.setattr(doctor.scion_cli, "discover", lambda: [other, configured])
    monkeypatch.setattr(doctor.fe, "which", lambda name: str(other))
    assert all(result.status == doctor.OK for result in doctor.check_scion_binaries())


def test_path_and_config_mismatch_warn_and_recommend_active_binary(locations, monkeypatch):
    configured, other = locations
    monkeypatch.setattr(doctor.scion_cli, "discover", lambda: [other, configured])
    monkeypatch.setattr(doctor.fe, "which", lambda name: str(other))
    results = doctor.check_scion_binaries()
    assert results[0].detail == str(other)
    assert any(result.status == doctor.WARN and "configured installation" in result.title
               for result in results)
    assert any(result.status == doctor.WARN and "recommendation" in result.title
               for result in results)


def test_installed_binary_off_path_has_export_command(locations, monkeypatch):
    configured, _ = locations
    monkeypatch.setattr(doctor.fe, "which", lambda name: None)
    failure = next(result for result in doctor.check_scion_binaries() if result.status == doctor.FAIL)
    assert str(configured) in failure.detail
    assert "export PATH='" in failure.fix[0]


def test_cli_check_runs_without_docker(locations, monkeypatch):
    monkeypatch.setattr(doctor.fe, "settings", lambda: {})
    for name in ("check_tools", "check_submodule"):
        monkeypatch.setattr(doctor, name, lambda: [])
    monkeypatch.setattr(doctor, "check_config", lambda values: [])
    monkeypatch.setattr(doctor, "check_docker", lambda: [doctor.Result(doctor.FAIL, "Docker")])
    sections = dict(doctor.all_checks(quick=True))
    assert "Scion CLI" in sections
    assert all(result.status == doctor.OK for result in sections["Scion CLI"])
