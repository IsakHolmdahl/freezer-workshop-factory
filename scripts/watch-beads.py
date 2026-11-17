#!/usr/bin/env python3
"""Scion Beads (`bd` / Dolt) CLI explorer and live monitor.

Discovers Scion project scratchpads and Dolt databases on 127.0.0.1:3307,
providing both a live Rich dashboard (`watch`) and interactive/CLI subcommands
for exploring projects, epics, task dependency trees, individual bead details,
comments, events, curated memories (`bd memories`), and friction/curation status.

Usage:
  ./scripts/watch-beads.py                        # live watch (default)
  ./scripts/watch-beads.py --once -p mtg          # snapshot once
  ./scripts/watch-beads.py projects               # list all discovered projects
  ./scripts/watch-beads.py list -p sm -s open     # list/filter beads
  ./scripts/watch-beads.py tree -p sm             # hierarchical work graph
  ./scripts/watch-beads.py show sf-1i1            # full bead details + comments + events
  ./scripts/watch-beads.py memories -p sm         # list curated project memories
  ./scripts/watch-beads.py events -p sm -n 20     # recent Dolt audit events / interactions
  ./scripts/watch-beads.py friction -p sm         # backlog curator trigger analysis
  ./scripts/watch-beads.py explore -p sm          # interactive REPL explorer
"""

from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import os
import factory_env as fe
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any

import click
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree


STATUS_STYLE = {
    "open": ("OPEN", "bold cyan"),
    "in_progress": ("IN_PROGRESS", "bold yellow"),
    "hooked": ("HOOKED", "bold yellow"),
    "blocked": ("BLOCKED", "bold red"),
    "deferred": ("DEFERRED", "dim"),
    "closed": ("CLOSED", "bold green"),
}

LABEL_STYLE = {
    "friction": "bold yellow",
    "user-interjection": "bold magenta",
    "agent-failed": "bold red",
    "rework": "bold red",
    "escalation": "bold white on red",
    "curated": "bold green",
}


def find_credentials_and_projects(
    scion_home: Path, repo_root: Path, project_filter: str | None = None
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Discover Dolt connection defaults and per-project scratchpad Beads dirs."""
    default_conn = {
        "host": os.environ.get("BEADS_DOLT_SERVER_HOST", "127.0.0.1"),
        "port": os.environ.get("BEADS_DOLT_SERVER_PORT", "3307"),
        "user": os.environ.get("BEADS_DOLT_SERVER_USER", "beads"),
        "password": os.environ.get("BEADS_DOLT_PASSWORD")
        or os.environ.get("DOLT_DB_PASSWORD", ""),
    }

    for env_candidate in (repo_root / ".env.secrets", repo_root / ".env"):
        if not default_conn["password"] and env_candidate.is_file():
            for line in env_candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("DOLT_DB_PASSWORD="):
                    val = line.split("=", 1)[1].strip().strip("'\"")
                    if val and val != "<random>":
                        default_conn["password"] = val
                        break

    projects: list[dict[str, Any]] = []
    seen_dbs: set[str] = set()
    configs_root = scion_home / "project-configs"
    if configs_root.is_dir():
        for proj_dir in sorted(configs_root.iterdir()):
            if not proj_dir.is_dir():
                continue
            slug = proj_dir.name.rsplit("__", 1)[0] or proj_dir.name
            if project_filter and project_filter.lower() not in slug.lower():
                continue
            scratch = proj_dir / "shared-dirs" / "scratchpad"
            beads_dir = scratch / "beads"
            if not beads_dir.is_dir():
                continue

            target_file = beads_dir / "dolt-target.json"
            meta_file = beads_dir / "metadata.json"
            env_file = beads_dir / ".env"

            target: dict[str, Any] = {}
            if target_file.is_file():
                try:
                    target = json.loads(target_file.read_text(encoding="utf-8"))
                except ValueError:
                    target = {}
            elif meta_file.is_file():
                try:
                    m = json.loads(meta_file.read_text(encoding="utf-8"))
                    target = {
                        "host": m.get("dolt_server_host", default_conn["host"]),
                        "port": m.get("dolt_server_port", int(default_conn["port"])),
                        "user": m.get("dolt_server_user", default_conn["user"]),
                        "database": m.get("dolt_database", ""),
                    }
                except ValueError:
                    target = {}

            pw = default_conn["password"]
            if env_file.is_file():
                for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.startswith("BEADS_DOLT_PASSWORD="):
                        pw = line.split("=", 1)[1].strip()
                        break
            if not default_conn["password"] and pw:
                default_conn["password"] = pw

            db_name = str(target.get("database") or "")
            if not db_name:
                suffix = proj_dir.name.rsplit("__", 1)[-1]
                db_name = f"beads_{suffix}"

            trace_id = ""
            trace_file = beads_dir / "trace-id"
            if trace_file.is_file():
                trace_id = trace_file.read_text(encoding="utf-8", errors="ignore").strip()

            seen_dbs.add(db_name)
            projects.append(
                {
                    "slug": slug,
                    "config_dir": str(proj_dir),
                    "scratchpad": str(scratch),
                    "beads_dir": str(beads_dir),
                    "host": str(target.get("host") or default_conn["host"]),
                    "port": str(target.get("port") or default_conn["port"]),
                    "user": str(target.get("user") or default_conn["user"]),
                    "password": pw,
                    "database": db_name,
                    "trace_id": trace_id,
                }
            )

    if default_conn["password"]:
        for db in sorted(list_dolt_databases(default_conn)):
            if not db.startswith("beads_") or db in seen_dbs:
                continue
            if project_filter and project_filter.lower() not in db.lower():
                continue
            projects.append(
                {
                    "slug": db,
                    "config_dir": "",
                    "scratchpad": "",
                    "beads_dir": "",
                    "host": default_conn["host"],
                    "port": default_conn["port"],
                    "user": default_conn["user"],
                    "password": default_conn["password"],
                    "database": db,
                    "trace_id": "",
                }
            )

    return default_conn, projects


def run_dolt_sql(
    conn: dict[str, Any], query: str, database: str | None = None
) -> list[dict[str, Any]]:
    """Run a read-only SQL query against the Dolt server and return JSON rows."""
    pw = str(conn.get("password") or "")
    if not pw:
        return []
    cmd = [
        "dolt",
        "--host",
        str(conn.get("host", "127.0.0.1")),
        "--port",
        str(conn.get("port", "3307")),
        "--user",
        str(conn.get("user", "beads")),
        "--no-tls",
    ]
    if database:
        cmd.extend(["--use-db", database])
    cmd.extend(["sql", "-r", "json", "-q", query])
    env = dict(os.environ, DOLT_CLI_PASSWORD=pw)
    res = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=8, check=False)
    if res.returncode != 0 or not res.stdout.strip():
        return []
    try:
        parsed = json.loads(res.stdout)
        if isinstance(parsed, dict):
            return list(parsed.get("rows") or [])
    except ValueError:
        pass
    return []


def list_dolt_databases(conn: dict[str, Any]) -> set[str]:
    rows = run_dolt_sql(conn, "SHOW DATABASES;")
    return {str(r.get("Database") or "") for r in rows if r.get("Database")}


def list_running_agents() -> list[dict[str, str]]:
    """Return running Scion agent containers from docker ps."""
    res = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if res.returncode != 0:
        return []
    agents = []
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        name, image, status = parts
        if "--" not in name:
            continue
        project_slug, agent_name = name.split("--", 1)
        agents.append(
            {
                "project": project_slug,
                "name": agent_name,
                "container": name,
                "image": image,
                "status": status,
            }
        )
    return agents


def read_jsonl_tail(path: Path, limit: int = 8) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    items: list[dict[str, Any]] = []
    for line in lines[-limit:] if limit > 0 else lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                items.append(obj)
        except ValueError:
            continue
    return items


def read_dispatch_ledger(scratchpad: str, limit: int = 8) -> list[str]:
    if not scratchpad:
        return []
    root = Path(scratchpad)
    entries: list[str] = []
    for p in sorted(root.glob("*dispatched*.md")):
        try:
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip().startswith("- "):
                    entries.append(line.strip()[2:])
        except OSError:
            continue
    return entries[-limit:] if limit > 0 else entries


def _friction_module():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "skills" / "beads-orchestration" / "scripts" / "check-epic-friction.py"
    spec = importlib.util.spec_from_file_location("check_epic_friction", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate_friction(issues: list[dict[str, Any]]) -> dict[str, Any]:
    """Curation triggers, computed by the beads-orchestration skill's check-epic-friction.py."""
    return _friction_module().analyze(issues)


def parse_dt(val: Any) -> datetime | None:
    if not val or not isinstance(val, str):
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None


def fetch_project_state(
    proj: dict[str, Any],
    active_dbs: set[str],
    running_agents: list[dict[str, str]],
    event_limit: int = 10,
) -> dict[str, Any]:
    db = proj["database"]
    proj_agents = [a for a in running_agents if a["project"] == proj["slug"]]
    beads_dir = Path(proj["beads_dir"]) if proj.get("beads_dir") else None
    interactions = (
        read_jsonl_tail(beads_dir / "interactions.jsonl", limit=event_limit)
        if beads_dir
        else []
    )
    ledger = read_dispatch_ledger(proj.get("scratchpad", ""), limit=event_limit)

    if db not in active_dbs:
        return {
            **proj,
            "db_online": False,
            "running_agents": proj_agents,
            "issues": [],
            "memories": [],
            "comments": [],
            "events": [],
            "interactions": interactions,
            "dispatch_ledger": ledger,
            "friction": evaluate_friction([]),
        }

    issues_raw = run_dolt_sql(
        proj,
        "SELECT id, title, issue_type, status, priority, assignee, created_by, "
        "created_at, updated_at, closed_at, close_reason, notes, description "
        "FROM issues WHERE COALESCE(wisp_type, '') = '' ORDER BY created_at ASC, id ASC;",
        database=db,
    )
    deps_raw = run_dolt_sql(
        proj,
        "SELECT issue_id, depends_on_id, type FROM dependencies;",
        database=db,
    )
    labels_raw = run_dolt_sql(
        proj,
        "SELECT issue_id, label FROM labels ORDER BY label ASC;",
        database=db,
    )
    comments_raw = run_dolt_sql(
        proj,
        f"SELECT issue_id, author, text, created_at FROM comments ORDER BY created_at DESC LIMIT {int(max(12, event_limit))};",
        database=db,
    )
    events_raw = run_dolt_sql(
        proj,
        "SELECT issue_id, event_type, actor, old_value, new_value, comment, created_at "
        f"FROM events ORDER BY created_at DESC LIMIT {int(max(10, event_limit))};",
        database=db,
    )
    memories_raw = run_dolt_sql(
        proj,
        "SELECT `key`, `value` FROM config WHERE `key` LIKE 'memory.%' ORDER BY `key` ASC;",
        database=db,
    )

    labels_by_issue: dict[str, list[str]] = {}
    for row in labels_raw:
        iid = str(row.get("issue_id") or "")
        lbl = str(row.get("label") or "")
        if iid and lbl:
            labels_by_issue.setdefault(iid, []).append(lbl)

    parents_by_issue: dict[str, str] = {}
    blockers_by_issue: dict[str, list[str]] = {}
    discovered_by_issue: dict[str, list[str]] = {}
    for row in deps_raw:
        iid = str(row.get("issue_id") or "")
        dep = str(row.get("depends_on_id") or "")
        dtype = str(row.get("type") or "")
        if not iid or not dep:
            continue
        if dtype == "parent-child":
            parents_by_issue[iid] = dep
        elif dtype in ("blocks", "blocking"):
            blockers_by_issue.setdefault(iid, []).append(dep)
        elif dtype == "discovered-from":
            discovered_by_issue.setdefault(iid, []).append(dep)

    issues: list[dict[str, Any]] = []
    for row in issues_raw:
        iid = str(row.get("id") or "")
        parent_id = parents_by_issue.get(iid, "")
        if not parent_id and "." in iid:
            parent_id = iid.rsplit(".", 1)[0]
        c_dt = parse_dt(row.get("created_at"))
        u_dt = parse_dt(row.get("closed_at") or row.get("updated_at"))
        dur = (u_dt - c_dt).total_seconds() if (c_dt and u_dt and u_dt >= c_dt) else None
        issues.append(
            {
                "id": iid,
                "title": str(row.get("title") or ""),
                "type": str(row.get("issue_type") or "task"),
                "status": str(row.get("status") or "open"),
                "priority": int(row.get("priority") if row.get("priority") is not None else 2),
                "assignee": str(row.get("assignee") or ""),
                "created_by": str(row.get("created_by") or ""),
                "created_at": str(row.get("created_at") or ""),
                "updated_at": str(row.get("updated_at") or ""),
                "closed_at": str(row.get("closed_at") or ""),
                "close_reason": str(row.get("close_reason") or ""),
                "notes": str(row.get("notes") or ""),
                "description": str(row.get("description") or ""),
                "labels": labels_by_issue.get(iid, []),
                "parent_id": parent_id,
                "blocked_by": blockers_by_issue.get(iid, []),
                "discovered_from": discovered_by_issue.get(iid, []),
                "duration_sec": dur,
            }
        )

    memories = [
        {"key": str(r.get("key") or "").removeprefix("memory."), "value": str(r.get("value") or "")}
        for r in memories_raw
    ]

    return {
        **proj,
        "db_online": True,
        "running_agents": proj_agents,
        "issues": issues,
        "memories": memories,
        "comments": comments_raw,
        "events": events_raw,
        "interactions": interactions,
        "dispatch_ledger": ledger,
        "friction": evaluate_friction(issues),
    }


def load_all_states(
    scion_home: Path,
    repo_root: Path,
    project_filter: str | None = None,
    show_all: bool = False,
    event_limit: int = 10,
) -> tuple[dict[str, str], set[str], list[dict[str, str]], list[dict[str, Any]]]:
    default_conn, projects = find_credentials_and_projects(scion_home, repo_root, project_filter)
    active_dbs = list_dolt_databases(default_conn)
    running_agents = list_running_agents()
    states = [
        fetch_project_state(p, active_dbs, running_agents, event_limit=event_limit)
        for p in projects
    ]
    if not show_all and not project_filter:
        filtered = [
            s
            for s in states
            if s["running_agents"] or s["issues"] or s["interactions"] or s["dispatch_ledger"]
        ]
        if filtered:
            states = filtered
    return default_conn, active_dbs, running_agents, states


def format_issue_line(issue: dict[str, Any], running_names: set[str]) -> Text:
    line = Text()
    status_label, status_style = STATUS_STYLE.get(
        issue["status"], (issue["status"].upper(), "white")
    )
    line.append(f"[{status_label}]", style=status_style)
    line.append(f" {issue['id']} ", style="bold white")
    line.append(f"(P{issue['priority']} {issue['type']}) ", style="dim")
    line.append(issue["title"], style="bold" if issue["type"] == "epic" else "")

    assignee = issue.get("assignee") or ""
    if assignee:
        is_live = assignee in running_names
        badge = " ●" if is_live else ""
        line.append(f"  @{assignee}{badge}", style="bold green" if is_live else "cyan")

    for b in issue.get("blocked_by") or []:
        line.append(f"  [blocked-by:{b}]", style="red")

    for d in issue.get("discovered_from") or []:
        line.append(f"  [from:{d}]", style="magenta")

    for lbl in issue.get("labels") or []:
        style = LABEL_STYLE.get(lbl, "blue")
        line.append(f" #{lbl}", style=style)

    if issue.get("duration_sec") is not None and issue["status"] == "closed":
        line.append(f" ({int(issue['duration_sec'])}s)", style="dim")

    note_snippet = (issue.get("close_reason") or issue.get("notes") or "").strip()
    if note_snippet:
        first_line = note_snippet.splitlines()[-1].strip()
        if len(first_line) > 110:
            first_line = first_line[:107] + "..."
        line.append(f"\n    ↳ {first_line}", style="italic dim")

    return line


def render_work_tree(issues: list[dict[str, Any]], running_names: set[str], title: str = "Beads Work Graph") -> Tree:
    by_id = {i["id"]: i for i in issues}
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    roots: list[dict[str, Any]] = []
    for i in issues:
        pid = i.get("parent_id") or ""
        if pid and pid in by_id and pid != i["id"]:
            children_by_parent.setdefault(pid, []).append(i)
        else:
            roots.append(i)

    tree = Tree(Text(title, style="bold underline"))

    def _add_children(node: Tree, issue_obj: dict[str, Any], visited: set[str]) -> None:
        kids = children_by_parent.get(issue_obj["id"], [])
        for child in kids:
            if child["id"] in visited:
                continue
            visited.add(child["id"])
            c_kids = children_by_parent.get(child["id"], [])
            c_text = format_issue_line(child, running_names)
            if c_kids:
                closed_kids = sum(1 for k in c_kids if k["status"] == "closed")
                c_text.append(f"  [{closed_kids}/{len(c_kids)} closed]", style="bold yellow")
            c_node = node.add(c_text)
            _add_children(c_node, child, visited)

    visited_ids: set[str] = set()
    for root_issue in roots:
        visited_ids.add(root_issue["id"])
        kids = children_by_parent.get(root_issue["id"], [])
        root_text = format_issue_line(root_issue, running_names)
        if kids:
            closed_kids = sum(1 for k in kids if k["status"] == "closed")
            root_text.append(f"  [{closed_kids}/{len(kids)} closed]", style="bold yellow")
        r_node = tree.add(root_text)
        _add_children(r_node, root_issue, visited_ids)

    return tree


def format_event_detail(ev: dict[str, Any]) -> str:
    detail = ""
    old_v = str(ev.get("old_value") or "").strip()
    new_v = str(ev.get("new_value") or "").strip()
    if old_v.startswith("{") or new_v.startswith("{"):
        try:
            o_obj = json.loads(old_v) if old_v.startswith("{") else {}
            n_obj = json.loads(new_v) if new_v.startswith("{") else {}
            changed = [k for k in n_obj if o_obj.get(k) != n_obj.get(k)]
            if "notes" in changed and isinstance(n_obj.get("notes"), str):
                last_note = n_obj["notes"].strip().splitlines()[-1] if n_obj["notes"].strip() else ""
                detail = f"notes: {last_note}"
            elif changed:
                detail = "changed: " + ", ".join(changed)
        except ValueError:
            detail = f"{old_v} → {new_v}"
    elif old_v or new_v:
        detail = f"{old_v} → {new_v}".strip(" →")
    if ev.get("comment"):
        c = str(ev["comment"]).splitlines()[0]
        detail = f"{detail} ({c})".strip()
    return " ".join(detail.split())


def render_project_panel(state: dict[str, Any]) -> Panel:
    slug = state["slug"]
    db = state["database"]
    online = state["db_online"]
    trace_id = state.get("trace_id") or "-"
    running_agents = state.get("running_agents") or []
    running_names = {a["name"] for a in running_agents}
    issues = state.get("issues") or []

    counts = {"open": 0, "in_progress": 0, "blocked": 0, "closed": 0}
    for i in issues:
        st = i["status"]
        counts[st] = counts.get(st, 0) + 1

    header = Text()
    header.append(f"Project: {slug}", style="bold white")
    header.append(f"   DB: {db} ", style="dim")
    header.append("● ONLINE" if online else "○ OFFLINE", style="bold green" if online else "bold red")
    header.append(f"   Trace: {trace_id}", style="magenta")
    header.append(
        f"   Beads: {len(issues)} total ("
        f"{counts.get('open', 0)} open, "
        f"{counts.get('in_progress', 0)} active, "
        f"{counts.get('blocked', 0)} blocked, "
        f"{counts.get('closed', 0)} closed)",
        style="cyan",
    )

    sections: list[Any] = [header]

    if running_agents:
        ag_table = Table(box=box.SIMPLE, show_header=True, header_style="bold dim", pad_edge=False)
        ag_table.add_column("Running Agent", style="bold green")
        ag_table.add_column("Harness Image", style="dim")
        ag_table.add_column("Container Status")
        for a in running_agents:
            ag_table.add_row(f"● {a['name']}", a["image"], a["status"])
        sections.append(ag_table)

    if issues:
        sections.append(render_work_tree(issues, running_names))
    else:
        sections.append(
            Text("No beads currently stored in Dolt for this project.", style="dim italic")
        )

    friction = state["friction"]
    fric_text = Text("Backlog Curator Trigger: ", style="bold")
    if friction["triggered"]:
        fric_text.append(f"TRIGGERED ({friction['summary']})", style="bold yellow")
    else:
        fric_text.append(friction["summary"], style="green")
    sections.append(fric_text)

    memories = state.get("memories") or []
    if memories:
        mem_table = Table(
            title="Curated Project Memories (bd memories)",
            title_style="bold magenta",
            box=box.SIMPLE,
            show_header=True,
            pad_edge=False,
        )
        mem_table.add_column("Memory Key", style="bold magenta", no_wrap=True)
        mem_table.add_column("Rule / Lesson")
        for m in memories:
            mem_table.add_row(m["key"], m["value"])
        sections.append(mem_table)

    events = state.get("events") or []
    interactions = state.get("interactions") or []
    if events:
        ev_table = Table(
            title="Recent Beads Events (Live Dolt Audit Trail)",
            title_style="bold cyan",
            box=box.SIMPLE,
            show_header=True,
            pad_edge=False,
            expand=True,
        )
        ev_table.add_column("Timestamp", style="dim", no_wrap=True)
        ev_table.add_column("Bead", style="bold white", no_wrap=True)
        ev_table.add_column("Actor", style="cyan", no_wrap=True, max_width=24, overflow="ellipsis")
        ev_table.add_column("Event", no_wrap=True)
        ev_table.add_column("Details", ratio=1, no_wrap=True, overflow="ellipsis")
        for ev in events[:6]:
            detail = format_event_detail(ev)
            if len(detail) > 96:
                detail = detail[:93] + "..."
            ev_table.add_row(
                str(ev.get("created_at") or "")[:19].replace("T", " "),
                str(ev.get("issue_id") or ""),
                str(ev.get("actor") or ""),
                str(ev.get("event_type") or ""),
                detail,
            )
        sections.append(ev_table)
    elif interactions:
        int_table = Table(
            title="Recent Beads Interactions (Scratchpad Log)",
            title_style="bold cyan",
            box=box.SIMPLE,
            show_header=True,
            pad_edge=False,
        )
        int_table.add_column("Timestamp", style="dim", no_wrap=True)
        int_table.add_column("Bead", style="bold white", no_wrap=True)
        int_table.add_column("Actor", style="cyan", no_wrap=True)
        int_table.add_column("Transition")
        for it in interactions[-6:]:
            extra = it.get("extra") or {}
            trans = f"{extra.get('field', 'status')}: {extra.get('old_value', '')} → {extra.get('new_value', '')}"
            if extra.get("reason"):
                r = str(extra["reason"])
                trans += f" ({r[:80]}{'...' if len(r) > 80 else ''})"
            int_table.add_row(
                str(it.get("created_at") or "")[:19].replace("T", " "),
                str(it.get("issue_id") or ""),
                str(it.get("actor") or ""),
                trans,
            )
        sections.append(int_table)

    return Panel(
        Group(*sections),
        title=f"[bold]{slug}[/bold]",
        border_style="green" if running_agents else ("cyan" if issues else "dim"),
    )


def build_dashboard(
    scion_home: Path,
    repo_root: Path,
    project_filter: str | None,
    show_all: bool,
) -> tuple[list[dict[str, Any]], Group]:
    default_conn, active_dbs, running_agents, states = load_all_states(
        scion_home, repo_root, project_filter=project_filter, show_all=show_all
    )

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    top_bar = Panel(
        Text.from_markup(
            f"[bold white]Scion Beads Live Monitor[/bold white]   "
            f"[dim]Dolt Server:[/dim] [cyan]{default_conn['host']}:{default_conn['port']}[/cyan]   "
            f"[dim]Databases:[/dim] [green]{len(active_dbs)}[/green]   "
            f"[dim]Running Agents:[/dim] [green]{len(running_agents)}[/green]   "
            f"[dim]Updated:[/dim] {now_str}"
        ),
        style="on grey11",
        box=box.ROUNDED,
    )

    panels = [render_project_panel(s) for s in states]
    if not panels:
        panels = [
            Panel(
                Text("No matching Scion projects with Beads state found.", style="yellow"),
                title="Empty",
            )
        ]

    return states, Group(top_bar, *panels)


def filter_issues(
    issues: list[dict[str, Any]],
    *,
    status: str | None = None,
    issue_type: str | None = None,
    label: str | None = None,
    assignee: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    out = []
    for item in issues:
        if status and item.get("status", "").lower() != status.lower():
            continue
        if issue_type and item.get("type", "").lower() != issue_type.lower():
            continue
        if label and label.lower() not in {l.lower() for l in (item.get("labels") or [])}:
            continue
        if assignee and assignee.lower() not in (item.get("assignee") or "").lower():
            continue
        if search:
            q = search.lower()
            haystack = " ".join(
                [
                    str(item.get("id") or ""),
                    str(item.get("title") or ""),
                    str(item.get("description") or ""),
                    str(item.get("notes") or ""),
                    str(item.get("close_reason") or ""),
                ]
            ).lower()
            if q not in haystack:
                continue
        out.append(item)
    return out


def fetch_bead_detail(
    states: list[dict[str, Any]], bead_id: str
) -> list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]]:
    """Find a bead by exact ID or suffix across projects and fetch all its comments & events."""
    matches = []
    q = bead_id.strip().lower()
    for state in states:
        for issue in state.get("issues") or []:
            iid = issue["id"].lower()
            if iid == q or iid.endswith(q):
                safe_id = issue["id"].replace("'", "''")
                db = state["database"]
                comments = (
                    run_dolt_sql(
                        state,
                        f"SELECT author, text, created_at FROM comments WHERE issue_id = '{safe_id}' ORDER BY created_at ASC;",
                        database=db,
                    )
                    if state.get("db_online")
                    else []
                )
                events = (
                    run_dolt_sql(
                        state,
                        "SELECT event_type, actor, old_value, new_value, comment, created_at "
                        f"FROM events WHERE issue_id = '{safe_id}' ORDER BY created_at ASC;",
                        database=db,
                    )
                    if state.get("db_online")
                    else []
                )
                if not events and state.get("interactions"):
                    events = [
                        it
                        for it in state["interactions"]
                        if str(it.get("issue_id") or "").lower() == iid
                    ]
                matches.append((state, issue, comments, events))
        # Also check offline scratchpad interactions if DB is offline or issue not in Dolt
        if not state.get("issues") and state.get("interactions"):
            int_matches = [
                it
                for it in state["interactions"]
                if str(it.get("issue_id") or "").lower() == q
                or str(it.get("issue_id") or "").lower().endswith(q)
            ]
            if int_matches:
                first_id = str(int_matches[0].get("issue_id") or bead_id)
                last_extra = int_matches[-1].get("extra") or {}
                synthetic = {
                    "id": first_id,
                    "title": "(from scratchpad interactions log — Dolt DB offline)",
                    "type": "task",
                    "status": str(last_extra.get("new_value") or "unknown"),
                    "priority": 2,
                    "assignee": str(int_matches[-1].get("actor") or ""),
                    "created_by": str(int_matches[0].get("actor") or ""),
                    "created_at": str(int_matches[0].get("created_at") or ""),
                    "updated_at": str(int_matches[-1].get("created_at") or ""),
                    "closed_at": "",
                    "close_reason": str(last_extra.get("reason") or ""),
                    "notes": "",
                    "description": "",
                    "labels": [],
                    "parent_id": first_id.rsplit(".", 1)[0] if "." in first_id else "",
                    "blocked_by": [],
                    "discovered_from": [],
                    "duration_sec": None,
                }
                matches.append((state, synthetic, [], int_matches))
    return matches


def render_bead_detail_panel(
    state: dict[str, Any],
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> Panel:
    by_id = {i["id"]: i for i in (state.get("issues") or [])}
    children = [i for i in (state.get("issues") or []) if i.get("parent_id") == issue["id"]]
    running_names = {a["name"] for a in (state.get("running_agents") or [])}

    meta = Table.grid(padding=(0, 2))
    meta.add_column(style="bold dim", justify="right")
    meta.add_column()

    status_label, status_style = STATUS_STYLE.get(
        issue["status"], (issue["status"].upper(), "white")
    )
    meta.add_row("Project:", f"[bold]{state['slug']}[/bold] ([dim]{state['database']}[/dim])")
    meta.add_row("Bead ID:", f"[bold white]{issue['id']}[/bold white]")
    meta.add_row("Title:", f"[bold]{issue['title']}[/bold]")
    meta.add_row("Status:", f"[{status_style}]{status_label}[/{status_style}]")
    meta.add_row("Type / Priority:", f"{issue['type']} / P{issue['priority']}")
    if issue.get("assignee"):
        meta.add_row("Assignee:", f"[cyan]@{issue['assignee']}[/cyan]")
    if issue.get("created_by"):
        meta.add_row("Created By:", issue["created_by"])
    if issue.get("parent_id"):
        parent_title = by_id.get(issue["parent_id"], {}).get("title", "")
        meta.add_row(
            "Parent:",
            f"[bold]{issue['parent_id']}[/bold]" + (f" — {parent_title}" if parent_title else ""),
        )
    if issue.get("blocked_by"):
        meta.add_row("Blocked By:", ", ".join(issue["blocked_by"]))
    if issue.get("discovered_from"):
        meta.add_row("Discovered From:", ", ".join(issue["discovered_from"]))
    if issue.get("labels"):
        meta.add_row("Labels:", ", ".join(f"#{l}" for l in issue["labels"]))
    meta.add_row(
        "Timestamps:",
        f"created={issue.get('created_at') or '-'}  updated={issue.get('updated_at') or '-'}  closed={issue.get('closed_at') or '-'}",
    )
    if issue.get("duration_sec") is not None:
        meta.add_row("Duration:", f"{int(issue['duration_sec'])}s")

    sections: list[Any] = [meta]

    for field, label, style in (
        ("description", "Description", "cyan"),
        ("notes", "Notes", "yellow"),
        ("close_reason", "Close Reason / Summary", "green"),
    ):
        val = (issue.get(field) or "").strip()
        if val:
            sections.append(
                Panel(
                    Markdown(val),
                    title=f"[bold {style}]{label}[/bold {style}]",
                    border_style=style,
                    box=box.ROUNDED,
                )
            )

    if children:
        c_tree = Tree(Text(f"Child Beads ({len(children)})", style="bold underline"))
        for ch in children:
            c_tree.add(format_issue_line(ch, running_names))
        sections.append(c_tree)

    if comments:
        c_table = Table(
            title=f"Comments ({len(comments)})",
            title_style="bold magenta",
            box=box.SIMPLE,
            show_header=True,
            pad_edge=False,
            expand=True,
        )
        c_table.add_column("Timestamp", style="dim", no_wrap=True)
        c_table.add_column("Author", style="cyan", no_wrap=True)
        c_table.add_column("Comment", ratio=1)
        for c in comments:
            c_table.add_row(
                str(c.get("created_at") or "")[:19].replace("T", " "),
                str(c.get("author") or ""),
                str(c.get("text") or "").strip(),
            )
        sections.append(c_table)

    if events:
        e_table = Table(
            title=f"History / Events ({len(events)})",
            title_style="bold cyan",
            box=box.SIMPLE,
            show_header=True,
            pad_edge=False,
            expand=True,
        )
        e_table.add_column("Timestamp", style="dim", no_wrap=True)
        e_table.add_column("Actor", style="cyan", no_wrap=True)
        e_table.add_column("Event", no_wrap=True)
        e_table.add_column("Details", ratio=1)
        for ev in events:
            if "extra" in ev:
                extra = ev.get("extra") or {}
                ev_type = str(ev.get("kind") or extra.get("field") or "transition")
                detail = f"{extra.get('old_value', '')} → {extra.get('new_value', '')}"
                if extra.get("reason"):
                    detail += f" ({extra['reason']})"
            else:
                ev_type = str(ev.get("event_type") or "")
                detail = format_event_detail(ev)
            e_table.add_row(
                str(ev.get("created_at") or "")[:19].replace("T", " "),
                str(ev.get("actor") or ""),
                ev_type,
                detail,
            )
        sections.append(e_table)

    return Panel(
        Group(*sections),
        title=f"[bold white]{issue['id']}[/bold white] — {issue['title']}",
        border_style="cyan",
    )


class DefaultWatchGroup(click.Group):
    """Click group that defaults to the `watch` command when no subcommand is given."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not args or (args[0].startswith("-") and args[0] not in ("-h", "--help")):
            args = ["watch", *args]
        return super().parse_args(ctx, args)


@click.group(cls=DefaultWatchGroup, context_settings={"help_option_names": ["-h", "--help"]})
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Scion Beads (`bd` / Dolt) CLI explorer and live monitor.

    Run without a subcommand (or with `watch`) for the live dashboard, or use
    subcommands (`projects`, `list`, `tree`, `show`, `memories`, `events`,
    `friction`, `explore`) to inspect and explore Beads across projects.
    """
    ctx.ensure_object(dict)


def common_options(fn: Any) -> Any:
    fn = click.option(
        "--scion-home",
        default=lambda: str(fe.scion_home()),
        show_default="~/.scion",
        help="Path to ~/.scion directory.",
    )(fn)
    fn = click.option(
        "--json",
        "as_json",
        is_flag=True,
        help="Output results as JSON.",
    )(fn)
    fn = click.option(
        "-a",
        "--all",
        "show_all",
        is_flag=True,
        help="Include idle/empty projects.",
    )(fn)
    fn = click.option(
        "-p",
        "--project",
        help="Filter projects by slug substring (e.g. mtg, sm, order-client).",
    )(fn)
    return fn


def _repo_root() -> Path:
    here = Path(__file__).resolve().parent
    # Works from either <repo>/scripts/ or <repo>/scion/scripts/ (legacy)
    if here.parent.name == "scion":
        return here.parents[1]
    return here.parent


@cli.command("watch")
@common_options
@click.option(
    "-i",
    "--interval",
    type=float,
    default=2.0,
    show_default=True,
    help="Refresh interval in seconds for live watch mode.",
)
@click.option(
    "--once",
    is_flag=True,
    help="Print current Beads state once and exit.",
)
def cmd_watch(
    project: str | None,
    show_all: bool,
    as_json: bool,
    scion_home: str,
    interval: float,
    once: bool,
) -> None:
    """Live dashboard monitor (default command)."""
    shome = Path(scion_home).resolve()
    rroot = _repo_root()
    console = Console()

    if as_json or once:
        states, group = build_dashboard(shome, rroot, project, show_all)
        if as_json:
            click.echo(json.dumps(states, indent=2, default=str))
        else:
            console.print(group)
        return

    try:
        with Live(console=console, refresh_per_second=2, screen=False) as live:
            while True:
                _, group = build_dashboard(shome, rroot, project, show_all)
                live.update(group)
                time.sleep(max(0.5, interval))
    except KeyboardInterrupt:
        return


@cli.command("projects")
@common_options
def cmd_projects(
    project: str | None,
    show_all: bool,
    as_json: bool,
    scion_home: str,
) -> None:
    """List discovered Scion projects and their Beads/Dolt status."""
    shome = Path(scion_home).resolve()
    rroot = _repo_root()
    _, _, _, states = load_all_states(shome, rroot, project_filter=project, show_all=True)

    if not show_all and not project:
        non_empty = [
            s
            for s in states
            if s["db_online"] or s["running_agents"] or s["issues"] or s["interactions"]
        ]
        if non_empty:
            states = non_empty

    if as_json:
        summary = [
            {
                "slug": s["slug"],
                "database": s["database"],
                "db_online": s["db_online"],
                "trace_id": s["trace_id"],
                "running_agents": len(s["running_agents"]),
                "beads_total": len(s["issues"]),
                "memories_count": len(s["memories"]),
                "interactions_count": len(s["interactions"]),
                "friction_triggered": s["friction"]["triggered"],
            }
            for s in states
        ]
        click.echo(json.dumps(summary, indent=2))
        return

    console = Console()
    table = Table(title="Scion Projects — Beads Overview", box=box.ROUNDED, show_header=True)
    table.add_column("Project", style="bold white")
    table.add_column("Database", style="dim")
    table.add_column("DB Status")
    table.add_column("Agents", justify="right")
    table.add_column("Open", justify="right", style="cyan")
    table.add_column("Active", justify="right", style="yellow")
    table.add_column("Blocked", justify="right", style="red")
    table.add_column("Closed", justify="right", style="green")
    table.add_column("Memories", justify="right", style="magenta")
    table.add_column("Curator", style="bold")

    for s in states:
        issues = s["issues"]
        counts = {"open": 0, "in_progress": 0, "blocked": 0, "closed": 0}
        for i in issues:
            counts[i["status"]] = counts.get(i["status"], 0) + 1
        db_badge = "[bold green]● ONLINE[/bold green]" if s["db_online"] else "[bold red]○ OFFLINE[/bold red]"
        curator = (
            "[bold yellow]TRIGGERED[/bold yellow]"
            if s["friction"]["triggered"]
            else "[green]clean[/green]"
        )
        table.add_row(
            s["slug"],
            s["database"],
            db_badge,
            str(len(s["running_agents"])),
            str(counts.get("open", 0)),
            str(counts.get("in_progress", 0)),
            str(counts.get("blocked", 0)),
            str(counts.get("closed", 0)),
            str(len(s["memories"])),
            curator,
        )
    console.print(table)


@cli.command("list")
@common_options
@click.option("-s", "--status", help="Filter by status (open, in_progress, blocked, closed).")
@click.option("-t", "--type", "issue_type", help="Filter by issue type (epic, task, bug, etc.).")
@click.option("-l", "--label", help="Filter by label (e.g. rework, friction, escalation).")
@click.option("--assignee", help="Filter by assignee substring.")
@click.option("-q", "--search", help="Search substring in ID, title, description, or notes.")
def cmd_list(
    project: str | None,
    show_all: bool,
    as_json: bool,
    scion_home: str,
    status: str | None,
    issue_type: str | None,
    label: str | None,
    assignee: str | None,
    search: str | None,
) -> None:
    """List and filter beads across projects in a tabular view."""
    shome = Path(scion_home).resolve()
    rroot = _repo_root()
    _, _, _, states = load_all_states(shome, rroot, project_filter=project, show_all=True)

    rows: list[dict[str, Any]] = []
    for s in states:
        matched = filter_issues(
            s.get("issues") or [],
            status=status,
            issue_type=issue_type,
            label=label,
            assignee=assignee,
            search=search,
        )
        for item in matched:
            rows.append({"project": s["slug"], **item})

    if as_json:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    console = Console()
    if not rows:
        console.print("[yellow]No matching beads found in online Dolt databases.[/yellow]")
        return

    table = Table(title=f"Beads ({len(rows)} matched)", box=box.SIMPLE_HEAVY, show_header=True, pad_edge=False)
    table.add_column("Project", style="dim", no_wrap=True)
    table.add_column("ID", style="bold white", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Type", style="dim", no_wrap=True)
    table.add_column("Title", min_width=24)
    table.add_column("Assignee", style="cyan", no_wrap=True, max_width=20, overflow="ellipsis")

    for r in rows:
        st_label, st_style = STATUS_STYLE.get(r["status"], (r["status"].upper(), "white"))
        title_cell = f"[bold]{r['title']}[/bold]" if r["type"] == "epic" else r["title"]
        if r.get("labels"):
            lbl_str = " ".join(f"[blue]#{l}[/blue]" for l in r["labels"])
            title_cell = f"{title_cell}\n{lbl_str}"
        table.add_row(
            r["project"],
            r["id"],
            f"[{st_style}]{st_label}[/{st_style}]",
            f"P{r['priority']} {r['type']}",
            title_cell,
            r.get("assignee") or "-",
        )
    console.print(table)


@cli.command("tree")
@common_options
@click.option("-s", "--status", help="Filter by status.")
@click.option("-q", "--search", help="Filter by search query.")
def cmd_tree(
    project: str | None,
    show_all: bool,
    as_json: bool,
    scion_home: str,
    status: str | None,
    search: str | None,
) -> None:
    """Render the hierarchical Epic -> Task -> Subtask work graph."""
    shome = Path(scion_home).resolve()
    rroot = _repo_root()
    _, _, _, states = load_all_states(shome, rroot, project_filter=project, show_all=show_all)

    if as_json:
        click.echo(
            json.dumps(
                [{"project": s["slug"], "issues": s["issues"]} for s in states],
                indent=2,
                default=str,
            )
        )
        return

    console = Console()
    printed = False
    for s in states:
        issues = filter_issues(s.get("issues") or [], status=status, search=search)
        if not issues and not show_all:
            continue
        printed = True
        running_names = {a["name"] for a in (s.get("running_agents") or [])}
        if issues:
            tree = render_work_tree(issues, running_names, title=f"{s['slug']} ({len(issues)} beads)")
            console.print(tree)
        else:
            console.print(f"[dim]{s['slug']}: no matching beads[/dim]")
    if not printed:
        console.print("[yellow]No beads found to render.[/yellow]")


def _has_gum() -> bool:
    return bool(shutil.which("gum")) and sys.stdin.isatty() and sys.stdout.isatty()


def _gum_choose(header: str, options: list[str], height: int = 15) -> str | None:
    if not options:
        return None
    if _has_gum():
        res = subprocess.run(
            [
                "gum",
                "choose",
                "--header",
                header,
                "--height",
                str(height),
                "--cursor.foreground",
                "212",
                "--header.foreground",
                "99",
                *options,
            ],
            stdout=subprocess.PIPE,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            return None
        return res.stdout.rstrip("\r\n")
    console = Console()
    console.print(f"[bold magenta]{header}[/bold magenta]")
    for idx, opt in enumerate(options, 1):
        console.print(f"  [cyan]{idx}.[/cyan] {opt}")
    try:
        ans = console.input("[bold green]Select number (or Enter to cancel): [/bold green]").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if ans.isdigit() and 1 <= int(ans) <= len(options):
        return options[int(ans) - 1]
    return None


def _gum_filter(header: str, options: list[str], placeholder: str = "Type to fuzzy-filter...") -> str | None:
    if not options:
        return None
    if _has_gum():
        res = subprocess.run(
            [
                "gum",
                "filter",
                "--header",
                header,
                "--placeholder",
                placeholder,
                "--height",
                "18",
                "--indicator.foreground",
                "212",
                "--match.foreground",
                "212",
                "--header.foreground",
                "99",
            ],
            input="\n".join(options),
            stdout=subprocess.PIPE,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            return None
        return res.stdout.rstrip("\r\n")
    return _gum_choose(header, options)


def _gum_input(header: str, placeholder: str = "", value: str = "") -> str | None:
    if _has_gum():
        cmd = [
            "gum",
            "input",
            "--header",
            header,
            "--placeholder",
            placeholder,
            "--header.foreground",
            "99",
        ]
        if value:
            cmd.extend(["--value", value])
        res = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, check=False)
        if res.returncode != 0:
            return None
        return res.stdout.rstrip("\r\n")
    console = Console()
    try:
        return console.input(f"[bold magenta]{header}[/bold magenta] ({placeholder}): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None


def _gum_pager(renderable: Any) -> None:
    buf = io.StringIO()
    width = shutil.get_terminal_size((120, 40)).columns
    temp_console = Console(file=buf, force_terminal=True, color_system="truecolor", width=max(80, width - 4))
    temp_console.print(renderable)
    rendered = buf.getvalue()
    if _has_gum():
        subprocess.run(
            ["gum", "pager", "--soft-wrap", "--border", "rounded", "--border-foreground", "63"],
            input=rendered,
            text=True,
            check=False,
        )
    else:
        Console().print(renderable)


def _collect_Interactive_beads(states: list[dict[str, Any]]) -> list[tuple[str, str, dict[str, Any]]]:
    """Return (display_line, bead_id, project_state) for interactive gum filter."""
    items: list[tuple[str, str, dict[str, Any]]] = []
    for s in states:
        slug = s["slug"]
        for issue in s.get("issues") or []:
            iid = issue["id"]
            st = issue["status"].upper()
            tp = f"P{issue['priority']} {issue['type']}"
            assignee = f" @{issue['assignee']}" if issue.get("assignee") else ""
            labels = " ".join(f"#{l}" for l in (issue.get("labels") or []))
            label_part = f" [{labels}]" if labels else ""
            line = f"{iid:<12} | {st:<11} | {tp:<8} | {slug:<22} | {issue['title']}{assignee}{label_part}"
            items.append((line, iid, s))
        if not s.get("issues") and s.get("interactions"):
            seen: dict[str, dict[str, Any]] = {}
            for it in s["interactions"]:
                iid = str(it.get("issue_id") or "")
                if iid:
                    seen[iid] = it
            for iid, it in seen.items():
                extra = it.get("extra") or {}
                st = str(extra.get("new_value") or "log").upper()
                actor = str(it.get("actor") or "")
                reason = str(extra.get("reason") or "").splitlines()[0] if extra.get("reason") else "(scratchpad interaction)"
                line = f"{iid:<12} | {st:<11} | log      | {slug:<22} | {reason[:60]} @{actor}"
                items.append((line, iid, s))
    return items


def _pick_and_show_bead(states: list[dict[str, Any]], header: str = "Select a Bead to inspect (Esc to go back)") -> None:
    items = _collect_Interactive_beads(states)
    if not items:
        Console().print("[yellow]No beads or scratchpad interactions found in selected project(s).[/yellow]")
        return
    options = [line for line, _, _ in items]
    chosen = _gum_filter(header, options, placeholder="Type bead ID, title, status, agent, or label...")
    if not chosen:
        return
    bead_id = chosen.split("|", 1)[0].strip()
    matches = fetch_bead_detail(states, bead_id)
    if not matches:
        return
    panels = [render_bead_detail_panel(st, issue, comments, events) for st, issue, comments, events in matches]
    _gum_pager(Group(*panels))


@cli.command("show")
@click.argument("bead_id", required=False)
@common_options
def cmd_show(
    bead_id: str | None,
    project: str | None,
    show_all: bool,
    as_json: bool,
    scion_home: str,
) -> None:
    """Inspect full details, description, notes, comments, and events for BEAD_ID (interactive picker if omitted)."""
    shome = Path(scion_home).resolve()
    rroot = _repo_root()
    _, _, _, states = load_all_states(shome, rroot, project_filter=project, show_all=True)

    if not bead_id:
        _pick_and_show_bead(states)
        return

    matches = fetch_bead_detail(states, bead_id)
    if as_json:
        payload = [
            {
                "project": st["slug"],
                "issue": issue,
                "comments": comments,
                "events": events,
            }
            for st, issue, comments, events in matches
        ]
        click.echo(json.dumps(payload, indent=2, default=str))
        return

    console = Console()
    if not matches:
        console.print(f"[bold red]Bead '{bead_id}' not found in any discovered project.[/bold red]")
        raise SystemExit(1)

    for st, issue, comments, events in matches:
        console.print(render_bead_detail_panel(st, issue, comments, events))


@cli.command("memories")
@common_options
def cmd_memories(
    project: str | None,
    show_all: bool,
    as_json: bool,
    scion_home: str,
) -> None:
    """List curated project memories (`bd memories`) across projects."""
    shome = Path(scion_home).resolve()
    rroot = _repo_root()
    _, _, _, states = load_all_states(shome, rroot, project_filter=project, show_all=True)

    if as_json:
        click.echo(
            json.dumps(
                [{"project": s["slug"], "memories": s["memories"]} for s in states if s["memories"] or show_all],
                indent=2,
            )
        )
        return

    console = Console()
    any_mem = False
    for s in states:
        mems = s.get("memories") or []
        if not mems and not show_all:
            continue
        any_mem = True
        table = Table(
            title=f"{s['slug']} — Curated Memories ({len(mems)})",
            title_style="bold magenta",
            box=box.ROUNDED,
            show_header=True,
            expand=True,
        )
        table.add_column("Memory Key", style="bold magenta", no_wrap=True)
        table.add_column("Rule / Lesson", ratio=1)
        for m in mems:
            table.add_row(m["key"], m["value"])
        console.print(table)
    if not any_mem:
        console.print("[yellow]No curated memories found.[/yellow]")


@cli.command("events")
@common_options
@click.option("-n", "--limit", type=int, default=20, show_default=True, help="Max events per project.")
def cmd_events(
    project: str | None,
    show_all: bool,
    as_json: bool,
    scion_home: str,
    limit: int,
) -> None:
    """Show recent Dolt audit trail events and scratchpad interactions."""
    shome = Path(scion_home).resolve()
    rroot = _repo_root()
    _, _, _, states = load_all_states(
        shome, rroot, project_filter=project, show_all=True, event_limit=limit
    )

    if as_json:
        click.echo(
            json.dumps(
                [
                    {
                        "project": s["slug"],
                        "events": s["events"][:limit],
                        "interactions": s["interactions"][-limit:],
                    }
                    for s in states
                    if s["events"] or s["interactions"] or show_all
                ],
                indent=2,
                default=str,
            )
        )
        return

    console = Console()
    shown = False
    for s in states:
        events = (s.get("events") or [])[:limit]
        interactions = (s.get("interactions") or [])[-limit:]
        if not events and not interactions and not show_all:
            continue
        shown = True
        if events:
            ev_table = Table(
                title=f"{s['slug']} — Dolt Audit Trail ({len(events)} recent)",
                title_style="bold cyan",
                box=box.SIMPLE_HEAVY,
                show_header=True,
                expand=True,
            )
            ev_table.add_column("Timestamp", style="dim", no_wrap=True)
            ev_table.add_column("Bead", style="bold white", no_wrap=True)
            ev_table.add_column("Actor", style="cyan", no_wrap=True)
            ev_table.add_column("Event", no_wrap=True)
            ev_table.add_column("Details", ratio=1)
            for ev in events:
                ev_table.add_row(
                    str(ev.get("created_at") or "")[:19].replace("T", " "),
                    str(ev.get("issue_id") or ""),
                    str(ev.get("actor") or ""),
                    str(ev.get("event_type") or ""),
                    format_event_detail(ev),
                )
            console.print(ev_table)
        elif interactions:
            int_table = Table(
                title=f"{s['slug']} — Scratchpad Interactions ({len(interactions)} recent)",
                title_style="bold cyan",
                box=box.SIMPLE_HEAVY,
                show_header=True,
                expand=True,
            )
            int_table.add_column("Timestamp", style="dim", no_wrap=True)
            int_table.add_column("Bead", style="bold white", no_wrap=True)
            int_table.add_column("Actor", style="cyan", no_wrap=True)
            int_table.add_column("Transition", ratio=1)
            for it in interactions:
                extra = it.get("extra") or {}
                trans = f"{extra.get('field', 'status')}: {extra.get('old_value', '')} → {extra.get('new_value', '')}"
                if extra.get("reason"):
                    trans += f" ({extra['reason']})"
                int_table.add_row(
                    str(it.get("created_at") or "")[:19].replace("T", " "),
                    str(it.get("issue_id") or ""),
                    str(it.get("actor") or ""),
                    trans,
                )
            console.print(int_table)
    if not shown:
        console.print("[yellow]No events or interactions found.[/yellow]")


@cli.command("friction")
@common_options
def cmd_friction(
    project: str | None,
    show_all: bool,
    as_json: bool,
    scion_home: str,
) -> None:
    """Inspect uncurated friction, rework, and backlog-curator trigger status."""
    shome = Path(scion_home).resolve()
    rroot = _repo_root()
    _, _, _, states = load_all_states(shome, rroot, project_filter=project, show_all=True)

    if as_json:
        click.echo(
            json.dumps(
                [{"project": s["slug"], **s["friction"]} for s in states],
                indent=2,
            )
        )
        return

    console = Console()
    table = Table(title="Backlog Curator & Friction Status", box=box.ROUNDED, show_header=True)
    table.add_column("Project", style="bold white")
    table.add_column("Status")
    table.add_column("Interjections", justify="right")
    table.add_column("Failed / Rework", justify="right")
    table.add_column("Friction", justify="right")
    table.add_column("Summary")

    for s in states:
        f = s["friction"]
        if not s["issues"] and not show_all and not f["triggered"]:
            continue
        c = f["counts"]
        table.add_row(
            s["slug"],
            "[bold yellow]TRIGGERED[/bold yellow]" if f["triggered"] else "[green]CLEAN[/green]",
            str(c["user_interjection"]),
            str(c["failed_or_rework"]),
            str(c["friction"]),
            f["summary"],
        )
    console.print(table)


@cli.command("explore")
@click.option("-p", "--project", help="Initial project filter.")
@click.option(
    "--scion-home",
    default=lambda: str(fe.scion_home()),
    show_default="~/.scion",
    help="Path to ~/.scion directory.",
)
def cmd_explore(project: str | None, scion_home: str) -> None:
    """Interactive Charmbracelet `gum` TUI for exploring projects, trees, beads, memories, and events."""
    shome = Path(scion_home).resolve()
    rroot = _repo_root()
    console = Console()
    current_project = project
    current_status: str | None = None

    while True:
        _, active_dbs, running_agents, states = load_all_states(
            shome, rroot, project_filter=current_project, show_all=True, event_limit=40
        )
        total_beads = sum(len(s.get("issues") or []) for s in states)
        proj_label = current_project or f"all ({len(states)} projects)"
        status_label = current_status or "all"

        header = (
            f"Scion Beads Explorer  •  Project: {proj_label}  •  "
            f"Status: {status_label}  •  Beads: {total_beads}  •  "
            f"Running Agents: {len(running_agents)}"
        )

        actions = [
            "🔍 Fuzzy-Search & Inspect Bead (gum filter)",
            "🌲 View Work Graph Tree (Epic -> Task -> Subtask)",
            "📋 Browse / Filter Beads Table",
            "📁 Switch Project Filter",
            "🏷️  Filter by Bead Status",
            "🧠 View Curated Project Memories (bd memories)",
            "📜 View Audit Trail & Scratchpad Events",
            "⚠️  View Backlog-Curator & Friction Status",
            "📊 Full Dashboard Snapshot (Pager)",
            "📺 Launch Live Auto-Refreshing Watch",
            "🚪 Quit",
        ]

        choice = _gum_choose(header, actions, height=14)
        if not choice or choice.startswith("🚪"):
            break

        if choice.startswith("🔍"):
            filtered_states = states
            if current_status:
                filtered_states = [
                    {**s, "issues": filter_issues(s.get("issues") or [], status=current_status)}
                    for s in states
                ]
            _pick_and_show_bead(filtered_states)

        elif choice.startswith("🌲"):
            renderables: list[Any] = []
            for s in states:
                issues = filter_issues(s.get("issues") or [], status=current_status)
                if not issues:
                    continue
                r_names = {a["name"] for a in (s.get("running_agents") or [])}
                renderables.append(
                    render_work_tree(issues, r_names, title=f"{s['slug']} ({len(issues)} beads)")
                )
            if renderables:
                _gum_pager(Group(*renderables))
            else:
                console.print("[yellow]No beads match current filter.[/yellow]")

        elif choice.startswith("📋"):
            buf = io.StringIO()
            with subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "list",
                    *(["-p", current_project] if current_project else ["-a"]),
                    *(["-s", current_status] if current_status else []),
                    "--scion-home",
                    str(shome),
                ],
                stdout=subprocess.PIPE,
                text=True,
            ) as proc:
                out, _ = proc.communicate()
            if out.strip():
                _gum_pager(Text.from_ansi(out))

        elif choice.startswith("📁"):
            _, _, _, all_states = load_all_states(shome, rroot, project_filter=None, show_all=True)
            proj_opts = ["* All Projects"]
            for s in all_states:
                badge = "ONLINE" if s["db_online"] else "offline"
                n_beads = len(s.get("issues") or [])
                n_int = len(s.get("interactions") or [])
                proj_opts.append(f"{s['slug']:<32} | {badge:<7} | {n_beads} beads | {n_int} interactions")
            picked = _gum_filter("Select Project", proj_opts, placeholder="Type project name...")
            if picked:
                if picked.startswith("* All"):
                    current_project = None
                else:
                    current_project = picked.split("|", 1)[0].strip()

        elif choice.startswith("🏷️"):
            st_opts = ["* All Statuses", "open", "in_progress", "blocked", "closed", "deferred"]
            picked_st = _gum_choose("Select Status Filter", st_opts)
            if picked_st:
                current_status = None if picked_st.startswith("* All") else picked_st

        elif choice.startswith("🧠"):
            panels = []
            for s in states:
                mems = s.get("memories") or []
                if not mems:
                    continue
                t = Table(title=f"{s['slug']} — Curated Memories ({len(mems)})", box=box.ROUNDED, expand=True)
                t.add_column("Memory Key", style="bold magenta", no_wrap=True)
                t.add_column("Rule / Lesson", ratio=1)
                for m in mems:
                    t.add_row(m["key"], m["value"])
                panels.append(t)
            if panels:
                _gum_pager(Group(*panels))
            else:
                console.print("[yellow]No curated memories found.[/yellow]")

        elif choice.startswith("📜"):
            with subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "events",
                    *(["-p", current_project] if current_project else ["-a"]),
                    "-n",
                    "40",
                    "--scion-home",
                    str(shome),
                ],
                stdout=subprocess.PIPE,
                text=True,
            ) as proc:
                out, _ = proc.communicate()
            if out.strip():
                _gum_pager(Text.from_ansi(out))

        elif choice.startswith("⚠️"):
            with subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "friction",
                    *(["-p", current_project] if current_project else ["-a"]),
                    "--scion-home",
                    str(shome),
                ],
                stdout=subprocess.PIPE,
                text=True,
            ) as proc:
                out, _ = proc.communicate()
            if out.strip():
                _gum_pager(Text.from_ansi(out))

        elif choice.startswith("📊"):
            _, group = build_dashboard(shome, rroot, current_project, True)
            _gum_pager(group)

        elif choice.startswith("📺"):
            cmd_watch.callback(current_project, True, False, str(shome), 2.0, False)


@cli.command("i")
@click.option("-p", "--project", help="Initial project filter.")
@click.option(
    "--scion-home",
    default=lambda: str(fe.scion_home()),
    show_default="~/.scion",
    help="Path to ~/.scion directory.",
)
def cmd_interactive_alias(project: str | None, scion_home: str) -> None:
    """Alias for `explore` (interactive Charmbracelet `gum` TUI)."""
    cmd_explore.callback(project, scion_home)


def main(argv: list[str] | None = None) -> int:
    try:
        cli.main(args=argv, prog_name="watch-beads.py", standalone_mode=False)
        return 0
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1


if __name__ == "__main__":
    sys.exit(main())
