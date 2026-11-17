#!/usr/bin/env python3
"""Prometheus exporter for factory runs: reads Beads (Dolt) and publishes one set of
`asf_run_*` series per epic, so Grafana can group agent telemetry per coordinator task.

See TELEMETRY.md ("Runs"). Environment:
  DOLT_HOST, DOLT_PORT, DOLT_DB_USER, DOLT_DB_PASSWORD   Dolt SQL server
  RUN_EXPORTER_PORT        listen port (default 9105)
  RUN_EXPORTER_MAX_AGE_DAYS  ignore epics older than this (default 30)
  RUN_EXPORTER_ROLES_DIR   directory whose sub-directories are the role names (templates/)
"""
import logging
import os
import time
from pathlib import Path

import pymysql
from prometheus_client import REGISTRY, start_http_server
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

import runs

log = logging.getLogger("run-exporter")

QUERIES = {
    "issues": "SELECT id, title, issue_type, status, assignee, created_by, created_at, closed_at "
              "FROM issues WHERE COALESCE(wisp_type, '') = ''",
    "dependencies": "SELECT issue_id, depends_on_id, type FROM dependencies",
    "labels": "SELECT issue_id, label FROM labels",
    "events": "SELECT DISTINCT issue_id, actor FROM events",
}


def role_names(directory):
    path = Path(directory) if directory else None
    return sorted(p.name for p in path.iterdir() if p.is_dir()) if path and path.is_dir() else []


def read_runs(connection, roles, max_age_days):
    """All runs in every Beads database (`beads`, `beads_<project>`) on the server."""
    found = []
    with connection.cursor() as cursor:
        cursor.execute("SHOW DATABASES")
        databases = [row["Database"] for row in cursor.fetchall()]
        for database in sorted(d for d in databases if d == "beads" or d.startswith("beads_")):
            try:
                rows = {}
                for name, query in QUERIES.items():
                    cursor.execute(f"USE `{database}`")
                    cursor.execute(query)
                    rows[name] = cursor.fetchall()
            except pymysql.MySQLError as error:  # e.g. a database without a Beads schema yet
                log.debug("skipping %s: %s", database, error)
                continue
            project = database.removeprefix("beads_")
            found += runs.summarize(project, rows["issues"], rows["dependencies"],
                                    rows["labels"], rows["events"], roles)
    oldest = time.time() - max_age_days * 86400
    return [run for run in found if run.created >= oldest]


def metric_families(found):
    """Prometheus metric families for a list of runs."""
    run_labels = ["project", "run"]

    def gauge(name, text, extra=()):
        return GaugeMetricFamily(name, text, labels=[*run_labels, *extra])

    info = gauge("asf_run_info", "A factory run: one epic given to a coordinator.",
                 ["title", "status", "coordinator"])
    agent = gauge("asf_run_agent_info", "Agent that worked in a run; join on scion_agent_slug.",
                  ["scion_agent_slug", "role", "bead"])
    created = gauge("asf_run_created_timestamp_seconds", "When the epic was created.")
    closed = gauge("asf_run_closed", "1 when the epic is closed, else 0.")
    duration = gauge("asf_run_duration_seconds", "Epic created to closed (to now while open).")
    beads = gauge("asf_run_beads", "Beads under the epic.", ["type", "status"])
    labelled = gauge("asf_run_labelled_beads", "Beads in the run carrying a friction label.", ["label"])
    agents = gauge("asf_run_agents", "Agents that worked in the run, per role.", ["role"])

    for run in found:
        key = [run.project, run.run]
        info.add_metric([*key, run.title, run.status, run.coordinator], 1)
        created.add_metric(key, run.created)
        closed.add_metric(key, 1 if run.closed else 0)
        duration.add_metric(key, run.duration)
        for (kind, status), count in sorted(run.beads.items()):
            beads.add_metric([*key, kind, status], count)
        for label in runs.FRICTION_LABELS:  # always present, so a clean run shows 0
            labelled.add_metric([*key, label], run.labels.get(label, 0))
        per_role = {}
        for item in run.agents:
            agent.add_metric([*key, item.slug, item.role, item.bead], 1)
            per_role[item.role] = per_role.get(item.role, 0) + 1
        for role, count in sorted(per_role.items()):
            agents.add_metric([*key, role], count)
    return [info, agent, created, closed, duration, beads, labelled, agents]


class RunCollector(Collector):
    def __init__(self, connect, roles, max_age_days):
        self.connect, self.roles, self.max_age_days = connect, roles, max_age_days

    def collect(self):
        up = GaugeMetricFamily("asf_run_exporter_up", "1 when the last read of Beads succeeded.")
        try:
            connection = self.connect()
            try:
                found = read_runs(connection, self.roles, self.max_age_days)
            finally:
                connection.close()
        except pymysql.MySQLError as error:
            log.error("cannot read Beads: %s", error)
            up.add_metric([], 0)
            yield up
            return
        up.add_metric([], 1)
        yield up
        yield from metric_families(found)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    env = os.environ

    def connect():
        return pymysql.connect(
            host=env.get("DOLT_HOST", "127.0.0.1"), port=int(env.get("DOLT_PORT", "3307")),
            user=env.get("DOLT_DB_USER", "beads"), password=env.get("DOLT_DB_PASSWORD", ""),
            cursorclass=pymysql.cursors.DictCursor, connect_timeout=5, read_timeout=10,
        )

    roles = role_names(env.get("RUN_EXPORTER_ROLES_DIR"))
    REGISTRY.register(RunCollector(connect, roles, float(env.get("RUN_EXPORTER_MAX_AGE_DAYS", "30"))))
    port = int(env.get("RUN_EXPORTER_PORT", "9105"))
    start_http_server(port)
    log.info("serving run metrics on :%s (%s roles known)", port, len(roles))
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
