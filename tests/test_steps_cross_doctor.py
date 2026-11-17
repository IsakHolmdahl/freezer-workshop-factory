"""Tests for the testcontainers, cross_project and doctor_step steps with fakes (no processes)."""
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest


from steps import cross_project, doctor_step, testcontainers  # noqa: E402
from steps.common import StepError  # noqa: E402
from test_steps_fakes import FakeAdapter, make_ctx  # noqa: E402,F401  (no_live_side_effects autouse)


class Failure(Exception):
    pass


# --- testcontainers ----------------------------------------------------------------

def fake_tc(enable=None, already=False):
    module = SimpleNamespace(Failure=Failure, enable=enable or (lambda interactive: 0))
    return module


def test_testcontainers_dry_run_enables_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(testcontainers, "_module", lambda: fake_tc(lambda interactive: called.append(1)))
    monkeypatch.setattr(testcontainers, "_already_enabled", lambda tc: False)
    ctx, _ = make_ctx(FakeAdapter(), dry_run=True)
    assert testcontainers.run(ctx).status == "dry-run"
    assert called == []


def test_testcontainers_skipped_when_already_enabled(monkeypatch):
    monkeypatch.setattr(testcontainers, "_module", lambda: fake_tc())
    monkeypatch.setattr(testcontainers, "_already_enabled", lambda tc: True)
    ctx, _ = make_ctx(FakeAdapter())
    assert testcontainers.run(ctx).status == "skipped"


def test_testcontainers_enable_passes_interactive_flag(monkeypatch):
    seen = []
    monkeypatch.setattr(testcontainers, "_module", lambda: fake_tc(lambda interactive: seen.append(interactive)))
    monkeypatch.setattr(testcontainers, "_already_enabled", lambda tc: False)
    ctx, _ = make_ctx(FakeAdapter(), assume_yes=True)
    assert testcontainers.run(ctx).status == "done"
    assert seen == [False]


def test_testcontainers_failure_is_step_error_with_fix(monkeypatch):
    def boom(interactive):
        raise Failure("rootless Docker socket did not appear")

    monkeypatch.setattr(testcontainers, "_module", lambda: fake_tc(boom))
    monkeypatch.setattr(testcontainers, "_already_enabled", lambda tc: False)
    ctx, _ = make_ctx(FakeAdapter())
    with pytest.raises(StepError) as error:
        testcontainers.run(ctx)
    assert "did not appear" in error.value.message
    assert error.value.fix


# --- cross_project -------------------------------------------------------------------

def fake_cp(projects, apply=None):
    return SimpleNamespace(Failure=Failure, projects=lambda: projects,
                           apply=apply or (lambda start=False: 0))


def test_cross_project_needs_running_scion(monkeypatch):
    monkeypatch.setattr(cross_project, "_hub_answers", lambda url: False)
    ctx, _ = make_ctx(FakeAdapter())
    with pytest.raises(StepError) as error:
        cross_project.run(ctx)
    assert "running Scion" in error.value.message
    assert error.value.fix


def test_cross_project_skipped_with_fewer_than_two_projects(monkeypatch):
    monkeypatch.setattr(cross_project, "_hub_answers", lambda url: True)
    monkeypatch.setattr(cross_project, "_module", lambda: fake_cp({"only": "1"}))
    ctx, _ = make_ctx(FakeAdapter())
    assert cross_project.run(ctx).status == "skipped"


def test_cross_project_applies_and_forwards_start(monkeypatch):
    seen = []
    monkeypatch.setattr(cross_project, "_hub_answers", lambda url: True)
    monkeypatch.setattr(cross_project, "_module", lambda: fake_cp({"a": "1", "b": "2"},
                                                                  apply=lambda start=False: seen.append(start)))
    ctx, _ = make_ctx(FakeAdapter())
    assert cross_project.run(ctx, start=True).status == "done"
    assert seen == [True]


def test_cross_project_unknown_project_is_rejected(monkeypatch):
    saved = []
    monkeypatch.setattr(cross_project, "_hub_answers", lambda url: True)
    monkeypatch.setattr(cross_project, "_module", lambda: fake_cp({"a": "1", "b": "2"}))
    monkeypatch.setattr(cross_project, "_save_selection",
                        lambda cp, slugs, known: saved.append(slugs))
    ctx, _ = make_ctx(FakeAdapter())
    cross_project.run(ctx, projects=["a", "b"])
    assert saved == [["a", "b"]]


def test_cross_project_dry_run_applies_nothing(monkeypatch):
    seen = []
    monkeypatch.setattr(cross_project, "_hub_answers", lambda url: True)
    monkeypatch.setattr(cross_project, "_module", lambda: fake_cp({"a": "1", "b": "2"},
                                                                  apply=lambda start=False: seen.append(1)))
    ctx, _ = make_ctx(FakeAdapter(), dry_run=True)
    assert cross_project.run(ctx).status == "dry-run"
    assert seen == []


def test_cross_project_dry_run_works_before_scion_is_started(monkeypatch):
    monkeypatch.setattr(cross_project, "_hub_answers", lambda url: False)
    ctx, _ = make_ctx(FakeAdapter(), dry_run=True)
    assert cross_project.run(ctx).status == "dry-run"


# --- doctor_step ------------------------------------------------------------------------

def install_fake_doctor(monkeypatch, sections, applied):
    module = types.ModuleType("doctor")
    module.OK, module.WARN, module.FAIL, module.SKIP = "ok", "warn", "FAIL", "skip"
    module.all_checks = lambda quick=False: iter(sections)
    module.print_results = lambda secs: any(r.status == "FAIL" for _, g in secs for r in g)
    module.apply_fixes = lambda secs: applied.append(True) or 1
    monkeypatch.setitem(sys.modules, "doctor", module)


def result(status, title, fix=()):
    return SimpleNamespace(status=status, title=title, detail="", fix=list(fix), autofix=None)


def test_doctor_passes_when_no_check_fails(monkeypatch):
    install_fake_doctor(monkeypatch, [("S", [result("ok", "docker")])], [])
    ctx, _ = make_ctx(FakeAdapter())
    assert doctor_step.run(ctx).status == "done"


def test_doctor_failure_is_step_error_with_fix(monkeypatch):
    install_fake_doctor(monkeypatch, [("S", [result("FAIL", "compose", ["upgrade docker compose"])])], [])
    ctx, _ = make_ctx(FakeAdapter())
    with pytest.raises(StepError) as error:
        doctor_step.run(ctx)
    assert "compose" in error.value.message
    assert error.value.fix == ["upgrade docker compose"]


def test_doctor_fix_applies_repairs_then_rechecks(monkeypatch):
    applied = []
    install_fake_doctor(monkeypatch, [("S", [result("ok", "docker")])], applied)
    ctx, _ = make_ctx(FakeAdapter())
    doctor_step.run(ctx, fix=True)
    assert applied == [True]


def test_doctor_dry_run_repairs_nothing(monkeypatch):
    applied = []
    install_fake_doctor(monkeypatch, [("S", [result("FAIL", "x")])], applied)
    ctx, _ = make_ctx(FakeAdapter(), dry_run=True)
    assert doctor_step.run(ctx, fix=True).status == "dry-run"
    assert applied == []
