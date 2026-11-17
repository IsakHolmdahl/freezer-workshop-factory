#!/usr/bin/env python3
"""Check whether an epic's beads crossed friction thresholds warranting `backlog-curator`.

Usage: python3 <this skill>/scripts/check-epic-friction.py <epic-id>

Optional environment variables (template or project env):
  CURATE_MIN_INTERJECTION  Minimum user-interjection beads to trigger (default: 1)
  CURATE_MIN_FAILED        Minimum failed/rework beads to trigger (default: 1)
  CURATE_MIN_FRICTION      Minimum friction-labeled beads to trigger (default: 1)
  CURATE_MAX_DURATION_SEC  Duration threshold in seconds for a friction bead, e.g.
                           "60" or "1m" or "90s" (default: 60, i.e. 1 minute)

Exits 0 and prints a one-line summary when curation should run; exits 1 otherwise.
"""
from datetime import datetime
import json
import os
import re
import subprocess
import sys


def parse_duration_seconds(raw, default=60):
    if not raw:
        return default
    raw = str(raw).strip().lower()
    m = re.fullmatch(r"(\d+)\s*([smh]?)", raw)
    if not m:
        return default
    val, unit = int(m.group(1)), m.group(2)
    return val * {"": 1, "s": 1, "m": 60, "h": 3600}[unit]


def parse_iso(ts):
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_seconds(item):
    if isinstance(item.get("duration_sec"), (int, float)):
        return int(item["duration_sec"])
    start = parse_iso(item.get("created_at"))
    end = parse_iso(item.get("closed_at") or item.get("updated_at"))
    return int((end - start).total_seconds()) if start and end else None


def analyze(issues, env=os.environ):
    """Curation analysis: {"triggered", "summary", "counts", "items"}."""
    min_interjection = int(env.get("CURATE_MIN_INTERJECTION", "1") or 1)
    min_failed = int(env.get("CURATE_MIN_FAILED", "1") or 1)
    min_friction = int(env.get("CURATE_MIN_FRICTION", "1") or 1)
    max_sec = parse_duration_seconds(
        env.get("CURATE_MAX_DURATION_SEC") or env.get("CURATE_MAX_DURATION"), 60
    )

    interjections, failed, friction, slow = [], [], [], []
    for item in issues:
        if not isinstance(item, dict):
            continue
        labels = set(item.get("labels") or [])
        if "curated" in labels:
            continue
        iid = str(item.get("id") or "")
        title = str(item.get("title") or "")
        notes = str(item.get("notes") or "")
        close_reason = str(item.get("close_reason") or "")
        combined_text = f"{notes}\n{close_reason}"
        if "user-interjection" in labels or "USER_INTERJECTION:" in combined_text:
            interjections.append(iid)
        if (labels & {"agent-failed", "rework", "escalation"}
                or title.startswith("Fix:")
                or "ATTEMPT_FAILED:" in combined_text):
            failed.append(iid)
        if "friction" in labels or "FRICTION:" in combined_text:
            friction.append(iid)
            seconds = _duration_seconds(item)
            if seconds is not None and seconds >= max_sec:
                slow.append(f"{iid}({seconds}s)")

    reasons = []
    if len(interjections) >= min_interjection:
        reasons.append(f"user_interjection={len(interjections)} [{','.join(interjections)}]")
    if len(failed) >= min_failed:
        reasons.append(f"failed/rework={len(failed)} [{','.join(failed)}]")
    if len(friction) >= min_friction:
        reasons.append(f"friction={len(friction)} [{','.join(friction)}]")
    if slow:
        reasons.append(f"slow_friction>={max_sec}s [{','.join(slow)}]")
    return {
        "triggered": bool(reasons),
        "summary": "; ".join(reasons) if reasons else "clean (below curation thresholds)",
        "counts": {"user_interjection": len(interjections), "failed_or_rework": len(failed),
                   "friction": len(friction)},
        "items": {"user_interjection": interjections, "failed_or_rework": failed,
                  "friction": friction, "slow_friction": slow},
    }


def evaluate(issues, env=os.environ):
    result = analyze(issues, env)
    return result["triggered"], result["summary"] if result["triggered"] else ""


def _has_friction_signal(item):
    if not isinstance(item, dict):
        return False
    labels = set(item.get("labels") or [])
    if "curated" in labels:
        return False
    title = str(item.get("title") or "")
    notes = str(item.get("notes") or "")
    close_reason = str(item.get("close_reason") or "")
    combined_text = f"{notes}\n{close_reason}"
    return bool(
        labels & {"user-interjection", "agent-failed", "rework", "escalation", "friction"}
        or title.startswith("Fix:")
        or "USER_INTERJECTION:" in combined_text
        or "ATTEMPT_FAILED:" in combined_text
        or "FRICTION:" in combined_text
    )


def load_epic_and_children(epic_id):
    items, seen = [], set()
    cmds = [["bd", "list", "--status", "all", "--json"]]
    if epic_id:
        cmds = [
            ["bd", "show", epic_id, "--json"],
            ["bd", "list", "--parent", epic_id, "--status", "all", "--json"],
        ]
    for cmd in cmds:
        out = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if out.returncode != 0:
            continue
        try:
            data = json.loads(out.stdout or "[]")
        except ValueError:
            continue
        for entry in (data if isinstance(data, list) else [data]):
            if isinstance(entry, dict) and entry.get("id") not in seen:
                seen.add(entry.get("id"))
                items.append(entry)
    # Also include any uncurated friction/rework beads created without --parent <epic-id>
    # (e.g., via --deps discovered-from:<task> or standalone beads in the project).
    if epic_id:
        out = subprocess.run(["bd", "list", "--status", "all", "--json"],
                             capture_output=True, text=True, check=False)
        if out.returncode == 0:
            try:
                all_data = json.loads(out.stdout or "[]")
            except ValueError:
                all_data = []
            for entry in (all_data if isinstance(all_data, list) else [all_data]):
                if isinstance(entry, dict) and entry.get("id") not in seen and _has_friction_signal(entry):
                    seen.add(entry.get("id"))
                    items.append(entry)
    return items


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    epic_id = argv[0] if (argv and not argv[0].startswith("-")) else None
    issues = load_epic_and_children(epic_id)
    should, summary = evaluate(issues)
    if should:
        print(f"CURATE_TRIGGERED: {summary}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
