"""Tests for the endpoints step (gateway model probe).

docker, the doctor probes and the prompts are faked; the autouse guard fails any real process.
"""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


import doctor  # noqa: E402
from steps import endpoints, ui  # noqa: E402
from steps.common import StepError  # noqa: E402
from test_steps_fakes import make_ctx  # noqa: E402,F401  (no_live_side_effects autouse)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(ui.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(ui, "ASSUME_YES", False)


def ok_process(code=0):
    return subprocess.CompletedProcess([], code, "", "")



# --- endpoints --------------------------------------------------------------------

def result(status, detail="", fix=()):
    return doctor.Result(status, "model", detail, list(fix))


def test_endpoints_all_answer(monkeypatch):
    monkeypatch.setattr(endpoints.fe, "gateway_models", lambda cfg=None: ["small", "large"])
    monkeypatch.setattr(endpoints.fe, "gateway_type", lambda cfg=None: "openai")
    monkeypatch.setattr(endpoints.doctor, "check_inference", lambda name: result(doctor.OK, "answers"))
    ctx, _ = make_ctx(None)
    assert endpoints.run(ctx).status == "done"


def test_endpoints_seed_the_mlflow_gateway_first(monkeypatch):
    calls = []
    monkeypatch.setattr(endpoints.fe, "gateway_models", lambda cfg=None: ["large"])
    monkeypatch.setattr(endpoints.fe, "gateway_type", lambda cfg=None: "mlflow")
    monkeypatch.setattr(endpoints.fe, "run", lambda argv, **kw: calls.append(argv) or ok_process())
    monkeypatch.setattr(endpoints.doctor, "check_inference", lambda name: result(doctor.OK))
    ctx, _ = make_ctx(None)
    endpoints.run(ctx)
    assert calls == [["docker", "exec", endpoints.MLFLOW_CONTAINER, "python", endpoints.SEED_SCRIPT]]


def test_endpoint_failure_with_yes_continues_and_reports(monkeypatch):
    monkeypatch.setattr(endpoints.fe, "gateway_models", lambda cfg=None: ["large"])
    monkeypatch.setattr(endpoints.fe, "gateway_type", lambda cfg=None: "openai")
    monkeypatch.setattr(endpoints.doctor, "check_inference",
                        lambda name: result(doctor.FAIL, "down", ["check the gateway"]))
    ctx, lines = make_ctx(None, assume_yes=True)
    outcome = endpoints.run(ctx)
    assert outcome.status == "done" and "1 gateway endpoint" in outcome.message


def test_endpoint_failure_without_yes_asks_and_stops_on_no(monkeypatch):
    monkeypatch.setattr(endpoints.fe, "gateway_models", lambda cfg=None: ["large"])
    monkeypatch.setattr(endpoints.fe, "gateway_type", lambda cfg=None: "openai")
    monkeypatch.setattr(endpoints.doctor, "check_inference", lambda name: result(doctor.FAIL, "down"))
    monkeypatch.setattr(ui, "ask", lambda question, default="": "n")
    ctx, _ = make_ctx(None)
    with pytest.raises(StepError) as error:
        endpoints.run(ctx)
    assert error.value.fix


def test_endpoints_dry_run_probes_nothing(monkeypatch):
    monkeypatch.setattr(endpoints.doctor, "check_inference", lambda name: pytest.fail("no probe in dry-run"))
    ctx, _ = make_ctx(None, dry_run=True)
    assert endpoints.run(ctx).status == "dry-run"


def test_endpoints_help_exits_zero():
    with pytest.raises(SystemExit) as exit_info:
        endpoints.main(["--help"])
    assert exit_info.value.code == 0
