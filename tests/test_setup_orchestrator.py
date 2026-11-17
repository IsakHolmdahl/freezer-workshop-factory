"""Tests for the setup orchestrator (scripts/setup.py): registry, step selection and error flow.

Step modules are replaced by fakes, Context.from_args is stubbed, and the autouse guard
from test_steps_fakes fails any test that starts a process.
"""
import argparse
import importlib
import sys
from types import SimpleNamespace

import pytest

import setup as orchestrator
from steps.common import StepError, StepResult
from deployments.base import DeployError
from test_steps_fakes import no_live_side_effects  # noqa: F401  (autouse guard)


class FakeStep:
    """Records each run() call; behaviour chosen per test."""

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def run(self, ctx, **options):
        self.calls.append((ctx, options))
        if self.error:
            raise self.error
        return StepResult("done", "fake done")


@pytest.fixture
def fakes(monkeypatch):
    """Map every step module name to a FakeStep and stub the context so no adapter is built."""
    modules = {}

    def fake_module(step):
        return modules.setdefault(step.module, FakeStep())

    monkeypatch.setattr(orchestrator, "_module", fake_module)
    monkeypatch.setattr(orchestrator.Context, "from_args",
                        classmethod(lambda cls, args, *, needs_adapter=True:
                                    SimpleNamespace(dry_run=args.dry_run, needs_adapter=needs_adapter)))
    monkeypatch.setattr(orchestrator.ui, "set_assume_yes", lambda value: None)
    return modules


def parse(argv):
    parser = orchestrator.build_parser()
    return parser, parser.parse_args(argv)


# --- registry -------------------------------------------------------------

def test_every_step_module_exists_and_exposes_run():
    for step in orchestrator.STEPS.values():
        module = importlib.import_module(f"steps.{step.module}")
        assert callable(getattr(module, "run", None)), step.name


def test_menu_tasks_are_used_by_some_step():
    used = {step.task for step in orchestrator.STEPS.values() if step.task}
    assert used == set(orchestrator.TASKS)


def test_steps_for_tasks_wraps_prerequisites_and_final_check():
    names = orchestrator.steps_for_tasks(["images"])
    assert names[:2] == orchestrator.PREREQUISITE_STEPS
    assert names[-1] == orchestrator.FINAL_STEP
    assert "build_images" in names and "build_scion" not in names
    assert "secrets" not in names


def test_github_token_setup_runs_after_hub_start_with_adapter():
    names = orchestrator.steps_for_tasks(["start", "publish"])
    assert names.index("scion_start") < names.index("secrets") < names.index("hub_wiring")
    assert orchestrator.STEPS["secrets"].needs_adapter


def test_scion_install_location_is_chosen_before_build():
    names = orchestrator.steps_for_tasks(["scion-build"])
    assert names.index("scion_cli") < names.index("build_scion")


# --- argument handling ----------------------------------------------------

def test_only_runs_exactly_the_listed_steps_in_order():
    parser, args = parse(["--only", "build_scion,services"])
    assert orchestrator.select_steps(args, parser) == ["build_scion", "services"]


def test_only_rejects_unknown_step_names(capsys):
    with pytest.raises(SystemExit):
        parse(["--only", "nope"])
    assert "unknown step(s): nope" in capsys.readouterr().err


def test_skip_removes_steps_but_never_prerequisites():
    parser, args = parse(["--yes", "--skip", "prerequisites,build_images,doctor"])
    names = orchestrator.select_steps(args, parser)
    assert "prerequisites" in names
    assert "build_images" not in names
    assert "doctor" not in names


def test_list_steps_prints_every_step(capsys):
    assert orchestrator.main(["--list-steps"]) == 0
    out = capsys.readouterr().out
    for name in orchestrator.STEPS:
        assert name in out


def test_non_tty_without_flags_refuses_the_menu(monkeypatch):
    monkeypatch.setattr(orchestrator.sys.stdin, "isatty", lambda: False, raising=False)
    parser, args = parse([])
    with pytest.raises(SystemExit):
        orchestrator.select_steps(args, parser)


def test_skip_without_yes_selects_all_remaining_steps_without_menu(monkeypatch):
    monkeypatch.setattr(orchestrator.sys.stdin, "isatty", lambda: False, raising=False)
    parser, args = parse(["--skip", "build_images"])
    assert orchestrator.select_steps(args, parser) == [
        name for name in orchestrator.STEPS if name != "build_images"]


# --- execution ------------------------------------------------------------

def test_run_steps_calls_each_module_in_order(fakes):
    rc = orchestrator.run_steps(["configuration", "services"], dry_run=True)
    assert rc == 0
    assert list(fakes) == ["configuration", "services"]
    assert all(fake.calls for fake in fakes.values())


def test_dry_run_is_passed_into_the_context(fakes):
    orchestrator.run_steps(["services"], dry_run=True)
    ctx, _ = fakes["services"].calls[0]
    assert ctx.dry_run is True


def test_step_error_stops_the_run_and_returns_1(fakes, capsys):
    fakes["services"] = FakeStep(error=StepError("docker is down", ["docker info"]))
    fakes["doctor_step"] = FakeStep()
    rc = orchestrator.run_steps(["services", "doctor"])
    assert rc == 1
    assert fakes["doctor_step"].calls == []  # later steps do not run
    out = capsys.readouterr().out
    assert "try: docker info" in out
    assert "./setup.py again" in out


def test_adapter_creation_error_is_reported_without_traceback(fakes, monkeypatch, capsys):
    def fail_context(*args, **kwargs):
        raise DeployError("scion.deployment is not set", fix=["./setup.py --deployment local"])

    monkeypatch.setattr(orchestrator.Context, "from_args", fail_context)
    assert orchestrator.run_steps(["scion_start"]) == 1
    out = capsys.readouterr().out
    assert "Setup stopped at scion_start" in out
    assert "try: ./setup.py --deployment local" in out
    assert not fakes


def test_step_deploy_error_stops_later_steps(fakes, capsys):
    fakes["services"] = FakeStep(error=DeployError("Docker is down", fix=["docker info"]))
    assert orchestrator.run_steps(["services", "doctor"]) == 1
    assert "doctor_step" not in fakes
    assert "try: docker info" in capsys.readouterr().out


def test_build_scion_step_passes_its_target(fakes):
    orchestrator.run_steps(["build_scion"])
    _, options = fakes["build"].calls[0]
    assert options == {"target": "scion"}


def test_main_reports_interrupt_as_130(monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(orchestrator, "run_steps", interrupted)
    assert orchestrator.main(["--only", "services"]) == 130


# --- menu and services task (ported from the old test_task_menu) -----------------

def test_menu_moves_cross_project_to_shim_and_keeps_experimental_docker_unticked(monkeypatch):
    seen = []
    monkeypatch.setattr(orchestrator.ui, "checkbox", lambda message, choices: seen.append(choices) or ["services"])
    assert orchestrator.choose_tasks() == ["services"]
    titles = [choice[1] for choice in seen[0]]
    assert titles == list(orchestrator.TASKS)
    ticked = {value for _, value, checked in seen[0] if checked}
    assert ticked == set(orchestrator.TASKS) - orchestrator.OPTIONAL_TASKS
    assert "testcontainers" not in ticked
    assert "cross-project" not in titles
    assert "cross_project" not in orchestrator.STEPS
    assert "experimental" in orchestrator.TASKS["testcontainers"]


def test_services_task_runs_services_and_endpoints(monkeypatch):
    names = orchestrator.steps_for_tasks(["services"])
    assert names == ["prerequisites", "configuration", "services", "copilot", "endpoints", "doctor"]


def test_yes_runs_every_task_once(monkeypatch):
    ran = []
    monkeypatch.setattr(orchestrator, "run_steps", lambda names, **kw: ran.append(names) or 0)
    assert orchestrator.main(["--yes"]) == 0
    assert ran == [orchestrator.steps_for_tasks(list(orchestrator.TASKS))]
