"""Turn Beads rows into per-run summaries. A run is one epic: one task given to a coordinator.

Pure functions (no database, no Prometheus) so they can be unit-tested; exporter.py
feeds them rows from Dolt and publishes the result.
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Labels the coordinator and workers put on beads (templates/coordinator/agents.md).
FRICTION_LABELS = ("rework", "friction", "agent-failed", "user-interjection", "escalation")
TITLE_LIMIT = 80


@dataclass
class Agent:
    slug: str
    role: str
    bead: str


@dataclass
class Run:
    project: str
    run: str                     # epic id
    title: str
    status: str
    coordinator: str
    created: float               # unix seconds
    closed: float | None
    duration: float              # closed - created, or now - created while open
    beads: Counter = field(default_factory=Counter)    # (issue_type, status) -> count
    labels: Counter = field(default_factory=Counter)   # friction label -> beads carrying it
    agents: list[Agent] = field(default_factory=list)


def timestamp(value):
    """Unix seconds from a Dolt datetime (datetime object or ISO text, UTC when naive)."""
    if isinstance(value, str) and value:
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def role_of(slug, bead, roles):
    """Role from a worker name `<bead-id>-<topic>-<role>`; roles may contain hyphens."""
    rest = slug[len(bead):] if slug.startswith(bead) else slug
    for role in sorted(roles, key=len, reverse=True):
        if rest == role or rest.endswith("-" + role):
            return role
    return rest.rsplit("-", 1)[-1] or "unknown"


def _parents(issues, dependencies):
    parents = {}
    for issue in issues:
        iid = str(issue.get("id") or "")
        if "." in iid:  # hierarchical ids: sf-4a2.1 is a child of sf-4a2
            parents[iid] = iid.rsplit(".", 1)[0]
    for dep in dependencies:
        if dep.get("type") == "parent-child":
            parents[str(dep.get("issue_id") or "")] = str(dep.get("depends_on_id") or "")
    return parents


def summarize(project, issues, dependencies, labels, events, roles=(), now=None):
    """One Run per epic in `issues`, oldest first.

    issues: id, title, issue_type, status, assignee, created_by, created_at, closed_at
    dependencies: issue_id, depends_on_id, type      labels: issue_id, label
    events: issue_id, actor
    """
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    by_id = {str(i.get("id") or ""): i for i in issues}
    parents = _parents(issues, dependencies)
    epics = {iid for iid, i in by_id.items() if i.get("issue_type") == "epic"}

    def epic_of(iid):
        seen = set()
        while iid and iid not in seen:
            if iid in epics:
                return iid
            seen.add(iid)
            iid = parents.get(iid, "")
        return None

    runs = {}
    for iid in epics:
        epic = by_id[iid]
        created = timestamp(epic.get("created_at")) or now
        closed = timestamp(epic.get("closed_at")) if epic.get("status") == "closed" else None
        coordinator = str(epic.get("assignee") or epic.get("created_by") or "")
        run = Run(project, iid, str(epic.get("title") or "")[:TITLE_LIMIT], str(epic.get("status") or "open"),
                  coordinator, created, closed, max(0.0, (closed or now) - created))
        if coordinator:
            run.agents.append(Agent(coordinator, "coordinator", iid))
        runs[iid] = run

    members = {}  # bead id -> run, for every bead under an epic (the epic included)
    for iid, issue in by_id.items():
        run = runs.get(epic_of(iid))
        if run is None:
            continue
        members[iid] = run
        if iid != run.run:
            run.beads[(str(issue.get("issue_type") or "task"), str(issue.get("status") or "open"))] += 1

    for iid, label in {(str(r.get("issue_id") or ""), str(r.get("label") or "")) for r in labels}:
        if iid in members and label in FRICTION_LABELS:
            members[iid].labels[label] += 1

    # Workers: whoever holds a bead, plus everyone who acted on it under a name that
    # starts with the bead id (a failed attempt loses its assignee but keeps its events).
    workers = {(iid, str(i.get("assignee") or "")) for iid, i in by_id.items()}
    workers |= {(str(e.get("issue_id") or ""), str(e.get("actor") or "")) for e in events
                if str(e.get("actor") or "").startswith(str(e.get("issue_id") or "") + "-")}
    for iid, slug in sorted(workers):
        run = members.get(iid)
        if run and slug and iid != run.run and slug != run.coordinator:
            run.agents.append(Agent(slug, role_of(slug, iid, roles), iid))

    ordered = sorted(runs.values(), key=lambda r: (r.created, r.run))
    # A PromQL join needs one run per agent: an agent seen in several runs counts for the newest.
    owner = {agent.slug: run.run for run in ordered for agent in run.agents}
    for run in ordered:
        unique = {a.slug: a for a in run.agents if owner[a.slug] == run.run}
        run.agents = list(unique.values())
    return ordered
