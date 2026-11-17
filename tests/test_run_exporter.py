"""Tests for the factory run exporter (telemetry/run-exporter)."""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "telemetry" / "run-exporter"))
import runs  # noqa: E402

ROLES = ["developer", "code-reviewer", "backlog-curator", "coordinator"]
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc).timestamp()


def issue(iid, kind="task", status="closed", assignee="", created_by="coord1",
          created="2026-11-16 10:00:00", closed="2026-11-16 10:30:00"):
    return {"id": iid, "title": f"Title {iid}", "issue_type": kind, "status": status, "assignee": assignee,
            "created_by": created_by, "created_at": created, "closed_at": closed if status == "closed" else None}


def summarize(issues, dependencies=(), labels=(), events=()):
    return runs.summarize("demo", issues, dependencies, labels, events, ROLES, now=NOW)


def test_run_groups_children_agents_and_labels():
    issues = [
        issue("sf-1", "epic", closed="2026-11-16 11:00:00"),
        issue("sf-1.1", assignee="sf-1.1-api-developer"),
        issue("sf-2", assignee="sf-2-api-code-reviewer"),
        issue("sf-3", "bug", status="open"),  # failed attempt: assignee cleared
        issue("sf-9"),                        # not part of any epic
    ]
    dependencies = [{"issue_id": "sf-2", "depends_on_id": "sf-1", "type": "parent-child"},
                    {"issue_id": "sf-3", "depends_on_id": "sf-1", "type": "parent-child"},
                    {"issue_id": "sf-3", "depends_on_id": "sf-2", "type": "discovered-from"}]
    labels = [{"issue_id": "sf-3", "label": "rework"}, {"issue_id": "sf-3", "label": "friction"},
              {"issue_id": "sf-1", "label": "user-interjection"}, {"issue_id": "sf-9", "label": "rework"},
              {"issue_id": "sf-2", "label": "unrelated"}]
    events = [{"issue_id": "sf-3", "actor": "sf-3-fix-developer"}, {"issue_id": "sf-3", "actor": "coord1"},
              {"issue_id": "sf-3", "actor": "someone-else"}]

    (run,) = summarize(issues, dependencies, labels, events)

    assert (run.run, run.status, run.coordinator, run.duration) == ("sf-1", "closed", "coord1", 3600)
    assert run.beads == {("task", "closed"): 2, ("bug", "open"): 1}
    assert run.labels == {"rework": 1, "friction": 1, "user-interjection": 1}
    assert {(a.slug, a.role, a.bead) for a in run.agents} == {
        ("coord1", "coordinator", "sf-1"),
        ("sf-1.1-api-developer", "developer", "sf-1.1"),
        ("sf-2-api-code-reviewer", "code-reviewer", "sf-2"),
        ("sf-3-fix-developer", "developer", "sf-3"),
    }


def test_open_run_counts_duration_until_now():
    (run,) = summarize([issue("sf-1", "epic", status="open", created="2026-11-16 11:50:00")])
    assert run.closed is None and run.duration == 600


def test_agent_seen_in_two_runs_belongs_to_the_newest():
    first, second = summarize([issue("sf-1", "epic"), issue("sf-2", "epic", created="2026-11-16 11:00:00")])
    assert first.agents == [] and [a.slug for a in second.agents] == ["coord1"]


@pytest.mark.parametrize("slug,bead,role", [
    ("sf-4a2-auth-developer", "sf-4a2", "developer"),
    ("sf-7-curator-backlog-curator", "sf-7", "backlog-curator"),
    ("sf-7-thing-mystery", "sf-7", "mystery"),
])
def test_role_of(slug, bead, role):
    assert runs.role_of(slug, bead, ROLES) == role


def test_metric_families_and_dashboard_agree():
    pytest.importorskip("pymysql")
    pytest.importorskip("prometheus_client")
    import exporter

    found = summarize([issue("sf-1", "epic"), issue("sf-1.1", assignee="sf-1.1-api-developer")])
    samples = {(s.name, tuple(sorted(s.labels.items()))): s.value
               for family in exporter.metric_families(found) for s in family.samples}
    key = (("project", "demo"), ("run", "sf-1"))
    assert samples[("asf_run_duration_seconds", key)] == 1800
    assert samples[("asf_run_labelled_beads", (("label", "rework"), *key))] == 0
    assert samples[("asf_run_agent_info", (("bead", "sf-1.1"), ("project", "demo"), ("role", "developer"),
                                           ("run", "sf-1"), ("scion_agent_slug", "sf-1.1-api-developer")))] == 1

    # every asf_* metric the dashboard queries is one the exporter publishes
    dashboard = (ROOT / "telemetry" / "grafana" / "dashboards" / "asf-run-comparison.json").read_text()
    json.loads(dashboard)
    published = {name for name, _ in samples}
    assert set(re.findall(r"asf_run_[a-z_]+", dashboard)) <= published
