"""Doctor uses the broker realignment planner; only --fix may apply its plan."""
from unittest.mock import Mock

import pytest

import doctor
from test_steps_fakes import no_live_side_effects  # noqa: F401


@pytest.fixture
def alignment(monkeypatch):
    module = doctor._broker_realigner()
    broker = {"id": "embedded", "name": "Hub broker", "status": "online",
              "labels": {"scion.io/broker-role": "embedded"}}
    state = {
        "brokers": [broker],
        "project": {"id": "p", "slug": "demo", "defaultRuntimeBrokerId": "old",
                    "providers": [{"brokerId": "old"}]},
        "agents": [],
    }

    def query(*args):
        if args == ("hub", "brokers"):
            return state["brokers"]
        if args == ("hub", "projects"):
            return [{"id": "global", "slug": "global"}, {"id": "p", "slug": "demo"}]
        if args == ("hub", "project", "info", "p"):
            return state["project"]
        if args == ("list", "--project", "p"):
            return state["agents"]
        pytest.fail(f"unexpected query: {args}")

    module.query = Mock(side_effect=query)
    module.main = Mock(return_value=0)
    monkeypatch.setattr(doctor, "_broker_realigner", lambda: module)
    return module, state


def test_drift_is_read_only_until_doctor_applies_fixes(alignment):
    module, _ = alignment
    result, = doctor.check_broker_alignment()
    assert result.status == doctor.WARN
    assert "2 change(s)" in result.detail
    assert "--apply" in result.fix[0]
    module.main.assert_not_called()
    assert doctor.apply_fixes([("Broker alignment", [result])]) == 1
    module.main.assert_called_once_with(["--apply", "--project", "p", "--broker", "embedded"])


def test_aligned_project_has_no_autofix(alignment):
    module, state = alignment
    state["project"].update(defaultRuntimeBrokerId="embedded", providers=[{"brokerId": "embedded"}])
    result, = doctor.check_broker_alignment()
    assert result.status == doctor.OK
    assert result.autofix is None
    module.main.assert_not_called()


def test_assigned_agents_keep_their_provider(alignment):
    _, state = alignment
    state["project"].update(defaultRuntimeBrokerId="embedded",
                            providers=[{"brokerId": "old"}, {"brokerId": "embedded"}])
    state["agents"] = [{"id": "a", "slug": "worker", "runtimeBrokerId": "old"}]
    result, = doctor.check_broker_alignment()
    assert result.status == doctor.OK
    assert "old (worker)" in result.detail
    assert result.autofix is None


def test_ambiguous_linked_project_requires_explicit_path(alignment, tmp_path):
    _, state = alignment
    providers = []
    for name in ("one", "two"):
        checkout = tmp_path / name
        (checkout / ".scion").mkdir(parents=True)
        providers.append({"brokerId": name, "localPath": str(checkout)})
    state["project"].update(gitRemote="example/repo",
                            labels={"scion.dev/project-origin": "linked"}, providers=providers)
    result, = doctor.check_broker_alignment()
    assert result.status == doctor.FAIL
    assert "conflicting checkout paths" in result.detail
    assert "--path" in result.fix[0]
    assert result.autofix is None


def test_external_broker_deployment_is_skipped(alignment):
    _, state = alignment
    state["brokers"] = [{"id": "external", "status": "online"}]
    result, = doctor.check_broker_alignment()
    assert result.status == doctor.SKIP


def test_offline_embedded_broker_is_reported_without_autofix(alignment):
    _, state = alignment
    state["brokers"][0]["status"] = "offline"
    result, = doctor.check_broker_alignment()
    assert result.status == doctor.FAIL
    assert "not online" in result.detail
    assert result.autofix is None


def test_unreachable_hub_is_reported(alignment):
    module, _ = alignment
    module.query.side_effect = ValueError("Hub unavailable")
    result, = doctor.check_broker_alignment()
    assert result.status == doctor.FAIL
    assert result.detail == "Hub unavailable"


def test_failed_repair_is_not_reported_as_success(alignment, capsys):
    module, _ = alignment
    module.main.return_value = 1
    results = doctor.check_broker_alignment()
    assert doctor.apply_fixes([("Broker alignment", results)]) == 0
    assert "broker realignment failed" in capsys.readouterr().out
