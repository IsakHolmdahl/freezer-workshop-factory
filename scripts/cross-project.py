#!/usr/bin/env python3
"""Enable cross-project delegation between coordinators, limited to selected projects.

Legacy standalone Python helper. The installed scion cross-project command uses
scion-cross-project.sh and JSON configuration instead. This script reads
`cross_project` from asf.yaml / asf.local.yaml and applies Scion's documented controls
(scion/docs-site/src/content/docs/reference/messaging-authorization.md):

  1. Hub switch `cross_project_messaging_enabled` (on while any project takes part).
  2. Inbound policy (`members`/`any`) on each selected project. Projects this
     script opened earlier and that are no longer selected go back to `none`.
     Other projects are left to their owners (only reported by `status`).
  3. Project variable ASF_PEER_PROJECTS ("slug=agent,...") on each selected
     project only, so its agents know the reachable peers (agents cannot list
     projects). Scion's checks still decide every send.
  4. With --start: the receiving agent (coordinator template, hub message mode)
     in each selected project that does not have it yet. Agents cannot create
     agents in other projects themselves (Scion forbids it).

Which projects the script manages is remembered in ~/.scion/asf-cross-project.json.

    cross-project.py setup             # legacy YAML project selection
    cross-project.py status            # what is configured on the Hub now
    cross-project.py apply [--start]   # apply saved asf.yaml settings
    cross-project.py disable           # close managed projects

Run on the host as the Hub admin. See docs/cross-project.md.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import factory_env as fe

PEERS_ENV = "ASF_PEER_PROJECTS"
STATE_FILE = fe.scion_home() / "asf-cross-project.json"


class Failure(Exception):
    pass


def scion(*args, check=True):
    result = fe.run(["scion", "--non-interactive", *args], timeout=120)
    if check and result.returncode != 0:
        raise Failure(f"scion {' '.join(args)}: {(result.stderr or result.stdout).strip()[:300]}")
    return result.stdout


# --- Hub state -----------------------------------------------------------------

def projects():
    """{slug: id} of all Hub projects except the built-in global one."""
    data = json.loads(scion("hub", "projects", "--format", "json") or "[]")
    return {p["slug"]: p["id"] for p in data if p.get("slug") != "global"}


def _field(text, name):
    match = re.search(rf"^\s*{name}:\s*(\S+)", text, re.MULTILINE)
    return match.group(1) if match else None


def _revision(text):
    match = re.search(r"\(revision (\d+)\)", text)
    return int(match.group(1)) if match else 0


def hub_switch():
    out = scion("hub", "messaging", "get")
    return _field(out, "cross_project_messaging_enabled") == "true", _revision(out)


def project_policy(project_id):
    out = scion("project", "messaging", "get", "--project", project_id)
    return _field(out, "cross_project_inbound"), _revision(out)


def set_hub_switch(enabled):
    current, revision = hub_switch()
    if current != enabled:
        scion("hub", "messaging", "set", f"--cross-project-enabled={str(enabled).lower()}",
              "--revision", str(revision))
        print(f"hub cross-project messaging: {'on' if enabled else 'off'}")


def set_policy(slug, project_id, policy):
    current, revision = project_policy(project_id)
    if current != policy:
        scion("project", "messaging", "set", "--project", project_id, "--policy", policy,
              "--revision", str(revision))
        print(f"{slug}: inbound {current} -> {policy}")


def set_peers(project_id, value):
    if value:
        scion("hub", "env", "set", f"--project={project_id}", "--always", PEERS_ENV, value)
    else:
        scion("hub", "env", "clear", f"--project={project_id}", PEERS_ENV, check=False)


def agents(project_id):
    return {a.get("slug") or a.get("name"): a for a in
            json.loads(scion("list", "--project", project_id, "--format", "json") or "[]")}


# --- configuration and state -----------------------------------------------------

def desired(cfg=None):
    """(peers {slug: agent}, inbound) from asf.yaml; empty peers when disabled."""
    cfg = cfg if cfg is not None else fe.config()
    peers = fe.get("cross_project.projects", {}, cfg) or {}
    if isinstance(peers, list):  # plain list of slugs: receiver "coordinator"
        peers = {slug: "coordinator" for slug in peers}
    inbound = fe.get("cross_project.inbound", "members", cfg)
    if inbound not in ("members", "any"):
        raise Failure(f"cross_project.inbound must be members or any, not {inbound!r}")
    if not fe.get("cross_project.enabled", False, cfg):
        peers = {}
    return {str(k): str(v) for k, v in peers.items()}, inbound


def managed():
    """Project slugs this script opened earlier."""
    try:
        return set(json.loads(STATE_FILE.read_text()).get("projects", []))
    except (OSError, ValueError):
        return set()


def save_managed(slugs):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"projects": sorted(slugs)}, indent=2) + "\n")


# --- commands -----------------------------------------------------------------

def setup(slugs=None, start=False, inbound=None):
    """Select communication peers, save the choice and apply the Hub policy."""
    from steps import ui

    known = projects()
    if len(known) < 2:
        raise Failure("at least two Hub projects are needed; register projects with Scion first")
    current, _ = desired()
    interactive = slugs is None
    if interactive:
        if not sys.stdin.isatty():
            raise Failure("no terminal for project selection; use --project SLUG at least twice")
        ui.info("Select projects whose coordinators may communicate with each other.")
        slugs = ui.checkbox("Which projects take part?",
                            [(slug, slug, slug in current) for slug in sorted(known)])
    unknown = sorted(set(slugs) - set(known))
    if unknown:
        raise Failure(f"unknown project(s): {', '.join(unknown)}")
    if len(set(slugs)) == 1:
        raise Failure("select at least two projects, or none to disable managed communication")
    if interactive and slugs and not start:
        start = ui.confirm("Start receiving coordinators where missing?", default=True)
    peers = {slug: current.get(slug, "coordinator") for slug in slugs}
    fe.set_local("cross_project.enabled", bool(peers))
    fe.set_local("cross_project.projects", peers)
    if inbound:
        fe.set_local("cross_project.inbound", inbound)
    return apply(start=start)

def apply(start=False):
    peers, inbound = desired()
    known = projects()
    unknown = sorted(set(peers) - set(known))
    if unknown:
        raise Failure(f"unknown project(s) in cross_project.projects: {', '.join(unknown)}; "
                      f"Hub projects: {', '.join(sorted(known))}")
    if len(peers) == 1:
        print("only one project selected: there is nothing to talk to, so nothing is opened")
        peers = {}
    previous = managed() & set(known)
    for slug in sorted(previous - set(peers)):  # deselected: close what we opened
        set_policy(slug, known[slug], "none")
        set_peers(known[slug], "")
    if peers:
        set_hub_switch(True)
        value = ",".join(f"{slug}={agent}" for slug, agent in sorted(peers.items()))
        for slug in sorted(peers):
            set_policy(slug, known[slug], inbound)
            set_peers(known[slug], value)
        print(f"{PEERS_ENV}={value} (on the selected projects)")
        _ensure_receivers(peers, known, start)
    elif not _others_open(known, set()):
        set_hub_switch(False)
    save_managed(set(peers))
    scion("hub", "env", "clear", "--scope", "hub", PEERS_ENV, check=False)  # Hub-wide value of earlier versions
    if peers:
        print("Coordinators started from now on get the peer list; recreate running ones to update it.")
    return 0


def _ensure_receivers(peers, known, start):
    for slug, agent in sorted(peers.items()):
        present = agents(known[slug]).get(agent)
        if present:
            print(f"{slug}: receiver {agent} exists ({present.get('phase', '?')})")
        elif start:
            scion("start", agent, "--project", known[slug], "--type", "coordinator", "--message-mode", "hub",
                  "Wait for delegated work from coordinators in other projects (cross-project-delegation skill).")
            print(f"{slug}: started {agent}")
        else:
            print(f"{slug}: no agent {agent} yet; start it with --start or: "
                  f"scion start {agent} --project {slug} --type coordinator")


def _others_open(known, exclude):
    """True when a project not managed by us still accepts cross-project messages."""
    return any(project_policy(pid)[0] not in (None, "none") for slug, pid in known.items() if slug not in exclude)


def disable():
    known = projects()
    for slug in sorted(managed() & set(known)):
        set_policy(slug, known[slug], "none")
        set_peers(known[slug], "")
    save_managed(set())
    if _others_open(known, set()):
        print("hub switch left on: projects not managed by this script still accept messages (see status)")
    else:
        set_hub_switch(False)
    print("cross-project delegation disabled for the managed projects")
    return 0


def status():
    enabled, revision = hub_switch()
    print(f"hub switch: {'on' if enabled else 'off'} (revision {revision})")
    peers, inbound = desired()
    ours = managed()
    print(f"asf config: inbound={inbound} projects={peers or '-'}")
    for slug, project_id in sorted(projects().items()):
        policy, _ = project_policy(project_id)
        note = "selected" if slug in peers else ("managed" if slug in ours else "owner's setting")
        if slug in peers:
            agent = agents(project_id).get(peers[slug])
            note += f", receiver {peers[slug]}: {agent.get('phase', '?') if agent else 'missing'}"
        print(f"  {slug:<30} inbound={policy:<8} ({note})")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scion cross-project", description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("command", nargs="?", default="setup", choices=("setup", "apply", "status", "disable"))
    parser.add_argument("--project", action="append", dest="projects", metavar="SLUG",
                        help="setup: participating project slug (repeatable)")
    parser.add_argument("--inbound", choices=("members", "any"), help="setup: inbound policy (default: keep current)")
    parser.add_argument("--start", action="store_true", help="apply: start missing receiving coordinators")
    args = parser.parse_args(argv)
    if args.command != "setup" and (args.projects is not None or args.inbound is not None):
        parser.error("--project and --inbound require the setup command")
    try:
        if args.command == "setup":
            return setup(args.projects, args.start, args.inbound)
        if args.command == "status":
            return status()
        if args.command == "disable":
            return disable()
        return apply(args.start)
    except Failure as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCross-project setup cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
