#!/usr/bin/env python3
"""Realign Hub projects to the embedded broker using the normal host Scion CLI.

Preview: python3 scripts/realign-brokers.py
Apply:   python3 scripts/realign-brokers.py --apply
Scope:   --project order-service-api (repeatable)
Path:    --path order-service-api=/absolute/path/to/checkout (repeatable)
"""

import argparse
import json
import shlex
from pathlib import Path
import subprocess
import sys


def scion(*args):
    result = subprocess.run(["scion", "--non-interactive", *args], cwd=Path.home(),
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError(f"scion {' '.join(args)}: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def query(*args):
    return json.loads(scion(*args, "--format", "json"))


def select_broker(brokers, reference):
    matches = [b for b in brokers if reference in (b.get("id"), b.get("slug"), b.get("name"))]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one broker matching {reference!r}; found {len(matches)}")
    broker = matches[0]
    if broker.get("status") != "online":
        raise ValueError(f"target broker {reference!r} is not online")
    if broker.get("labels", {}).get("scion.io/broker-role") != "embedded":
        raise ValueError("target must be the Hub's embedded broker")
    return broker


def checkout_root(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"checkout path must be absolute: {path}")
    if path.name == ".scion":
        path = path.parent
    path = path.resolve()
    if not path.is_dir() or not (path / ".scion").exists():
        raise ValueError(f"checkout must exist and contain a .scion marker: {path}")
    return str(path)


def plan(project, target, agents, explicit_path=None):
    providers = project.get("providers", [])
    own = next((p for p in providers if p["brokerId"] == target["id"]), None)
    path = None
    # Hub-managed projects should retain their managed storage, not inherit a
    # foreign broker's arbitrary directory. Checkout mapping is for linked git.
    if explicit_path:
        path = checkout_root(explicit_path)
    elif project.get("gitRemote") and project.get("labels", {}).get("scion.dev/project-origin") == "linked":
        candidates = {checkout_root(p["localPath"]) for p in providers if p.get("localPath")}
        if len(candidates) > 1:
            raise ValueError("conflicting checkout paths; specify --path project=/absolute/checkout")
        if not candidates:
            raise ValueError("linked Git project has no checkout path; specify --path project=/absolute/checkout")
        path = candidates.pop()
    commands = []
    current_path = checkout_root(own["localPath"]) if own and own.get("localPath") and path else None
    if not own or project.get("defaultRuntimeBrokerId") != target["id"] or (path and current_path != path):
        command = ["runtime-broker", "provide", "--project", project["id"],
                   "--broker", target["id"], "--make-default"]
        if path:
            command += ["--path", path]
        commands.append(command)
    kept = []
    for provider in providers:
        broker_id = provider["brokerId"]
        if broker_id == target["id"]:
            continue
        assigned = [a.get("slug") or a["id"] for a in agents
                    if a.get("runtimeBrokerId") == broker_id]
        if assigned:
            kept.append((provider.get("brokerName", broker_id), assigned))
        else:
            commands.append(["runtime-broker", "withdraw", "--project", project["id"], "--broker", broker_id])
    return commands, kept


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="apply changes (default: preview)")
    parser.add_argument("--broker", default="hosted-broker", help="embedded broker name, slug or ID")
    parser.add_argument("--project", action="append", default=[], help="project slug/ID; repeat to select several")
    parser.add_argument("--path", action="append", default=[], metavar="PROJECT=PATH", help="explicit checkout path")
    args = parser.parse_args(argv)
    paths = {}
    for value in args.path:
        key, separator, path = value.partition("=")
        if not separator or not key or not path:
            parser.error("--path must be PROJECT=/absolute/checkout")
        paths[key] = path
    target = select_broker(query("hub", "brokers"), args.broker)
    projects = query("hub", "projects")
    selected = set(args.project)
    known = {value for p in projects for value in (p["id"], p["slug"])}
    if selected - known:
        parser.error(f"unknown projects: {', '.join(sorted(selected - known))}")
    print(f"{'APPLY' if args.apply else 'PREVIEW'}: target {target['name']} ({target['id']})")
    failures = 0
    for row in projects:
        if row["slug"] == "global" or (selected and not selected.intersection((row["id"], row["slug"]))):
            continue
        try:
            project = query("hub", "project", "info", row["id"])
            agents = query("list", "--project", row["id"])
            commands, kept = plan(project, target, agents, paths.get(row["slug"], paths.get(row["id"])))
            print(f"\n{row['slug']}: {len(project.get('providers', []))} provider(s)")
            for broker, names in kept:
                print(f"  Keep {broker}: agents still assigned: {', '.join(names)}")
            for command in commands:
                print("  " + shlex.join(["scion", *command]))
                if args.apply:
                    scion(*command)
            if not commands:
                print("  No changes needed")
            if args.apply:
                actual = query("hub", "project", "info", row["id"])
                if actual.get("defaultRuntimeBrokerId") != target["id"]:
                    raise ValueError("default broker verification failed")
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            failures += 1
            print(f"  {row['slug']}: {error}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"Broker realignment failed: {error}", file=sys.stderr)
        raise SystemExit(1)
