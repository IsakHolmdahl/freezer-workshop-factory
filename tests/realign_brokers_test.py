"""Provider repair ordering, path ambiguity and existing-agent handling."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("realign_brokers", Path(__file__).resolve().parents[1] / "scripts" / "realign-brokers.py")
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


def project(path):
    return {"id": "project", "gitRemote": "example/repo", "labels": {"scion.dev/project-origin": "linked"},
            "defaultRuntimeBrokerId": "old", "providers": [
                {"brokerId": "new"}, {"brokerId": "old", "localPath": str(path / ".scion")}]}


def test_attach_checkout_before_withdraw_and_repeat_is_noop(tmp_path):
    (tmp_path / ".scion").mkdir()
    item = project(tmp_path)
    commands, kept = repair.plan(item, {"id": "new"}, [])
    assert commands[0][:2] == ["runtime-broker", "provide"]
    assert commands[0][-2:] == ["--path", str(tmp_path)]
    assert commands[1][:2] == ["runtime-broker", "withdraw"]
    assert not kept
    item["providers"] = [{"brokerId": "new", "localPath": str(tmp_path)}]
    item["defaultRuntimeBrokerId"] = "new"
    assert repair.plan(item, {"id": "new"}, []) == ([], [])


def test_provider_with_assigned_agents_is_retained(tmp_path):
    (tmp_path / ".scion").mkdir()
    commands, kept = repair.plan(project(tmp_path), {"id": "new"},
                                [{"id": "agent", "slug": "worker", "runtimeBrokerId": "old"}])
    assert len(commands) == 1
    assert kept == [("old", ["worker"])]


def test_conflicting_checkout_paths_need_override(tmp_path):
    (tmp_path / ".scion").mkdir()
    other = tmp_path / "other"
    (other / ".scion").mkdir(parents=True)
    item = project(tmp_path)
    item["providers"][0]["localPath"] = str(other)
    with pytest.raises(ValueError, match="conflicting"):
        repair.plan(item, {"id": "new"}, [])
    commands, _ = repair.plan(item, {"id": "new"}, [], str(tmp_path))
    assert commands[0][-1] == str(tmp_path)


def test_managed_project_does_not_inherit_checkout():
    commands, _ = repair.plan({"id": "p", "providers": [{"brokerId": "old", "localPath": "/unknown"}]},
                              {"id": "new"}, [])
    assert "--path" not in commands[0]


def test_target_must_be_online_embedded_broker():
    with pytest.raises(ValueError, match="embedded"):
        repair.select_broker([{"id": "a", "slug": "hosted-broker", "status": "online"}], "hosted-broker")
