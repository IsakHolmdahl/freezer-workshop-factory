#!/usr/bin/env python3
"""Remove everything ./setup.py created, so you can start fresh.

Lists each part it found and lets you tick what to delete. Containers and
volumes come from the core Compose project. Data volumes (Beads
issues in Dolt, MLflow traces, Grafana data) are never removed
unless you ask for them with --volumes or tick them in the menu.

    ./cleanup.py                  # menu: tick the parts to remove
    ./cleanup.py --dry-run        # only show what would be removed
    ./cleanup.py --skip config    # keep .env/.env.secrets
    ./cleanup.py --yes            # no questions; keeps the data volumes
    ./cleanup.py --yes --volumes  # no questions; also deletes the data volumes

Composable: plan() lists the parts, execute() removes the chosen ones. Both are
importable, so setup.py and doctor.py can reuse them.

The menu uses questionary from .venv; without it, it asks y/N per part.
"""
import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import factory_env as fe
from deployments.base import Resource, run_command
from deployments.local import LocalAdapter
from deployments.stack import COMPOSE_FILE, ComposeStack
from steps import ui
from scion_install import is_shim, upstream_binary

ROOT = fe.ROOT
NETWORK = "ai-software-factory-network"
SCION_HOME = fe.scion_home()
INSTALL_PATH = Path(os.environ.get("INSTALL_PATH") or fe.install_path())
# Images built by this repo (repository name without registry prefix).
BUILT_IMAGE_PREFIXES = ("asf-opencode", "scion-local-", "ai-software-factory-dolt")
BUILT_IMAGE_NAMES = ("scion",)


@dataclass
class Part:
    key: str
    title: str
    items: list[str]
    warning: str
    remove: Callable[[], object]
    destructive: bool = False     # data that cannot be recovered: needs --volumes or a ticked menu entry


def run(argv, timeout=300):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(argv, 1, "", str(error))


def lines(result):
    return [line for line in result.stdout.splitlines() if line.strip()]


def docker_ok():
    return run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=20).returncode == 0


def check(result):
    """Raise when a command (CompletedProcess or CommandResult) failed; None means nothing to check."""
    if result is not None and getattr(result, "returncode", 0) != 0:
        raise RuntimeError((getattr(result, "stderr", "") or getattr(result, "stdout", "")).strip()[:300])


def remove_resources(resources: list[Resource]) -> None:
    for resource in resources:
        check(resource.remove())


# --- what exists -----------------------------------------------------------

def agent_containers():
    return lines(run(["docker", "ps", "-a", "--filter", "label=scion.agent=true", "--format", "{{.Names}}"]))


def core_project() -> ComposeStack:
    """The core Compose project (MLflow, Dolt, telemetry)."""
    return ComposeStack([COMPOSE_FILE])


def core_volumes(stack) -> list[Resource]:
    """Named volumes of the core Compose project (data: always destructive)."""
    project = stack.project()
    if not project:
        return []
    names = lines(run(["docker", "volume", "ls", "--filter", f"label=com.docker.compose.project={project}",
                       "--format", "{{.Name}}"]))
    return [Resource("volume", name, True, lambda name=name: check(run(["docker", "volume", "rm", name])))
            for name in names]


def built_images():
    found = []
    for ref in lines(run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"])):
        repo = ref.rsplit(":", 1)[0].rsplit("/", 1)[-1]
        if repo.startswith(BUILT_IMAGE_PREFIXES) or repo in BUILT_IMAGE_NAMES:
            found.append(ref)
    return found


def existing(paths):
    return [p for p in paths if p.exists() or p.is_symlink()]


def remove_scion_home():
    default = Path.home() / ".scion"
    alias = default != SCION_HOME and default.is_symlink() and default.resolve() == SCION_HOME.resolve()
    remove_tree(SCION_HOME)
    if alias:
        default.unlink()


# --- plan ------------------------------------------------------------------

def plan(cfg=None, *, has_docker=None) -> list[Part]:
    """Parts that exist right now, in removal order (containers before volumes, volumes before images)."""
    cfg = fe.config() if cfg is None else cfg
    has_docker = docker_ok() if has_docker is None else has_docker
    parts = []
    local = LocalAdapter(cfg)
    if local.status().running:
        parts.append(Part("server", "Stop the local Scion server", ["scion server stop"], "",
                          lambda: check(local.scion(["server", "stop"], timeout=120))))
    if has_docker:
        agents = agent_containers()
        if agents:
            parts.append(Part("agents", "Remove Scion agent containers", agents,
                              "running agents are stopped; uncommitted work in their containers is lost",
                              lambda: check(run(["docker", "rm", "-f", *agents]))))
        # Core stack: one `compose down` stops every service; its volumes come from the adapter.
        stack = core_project()
        services = stack.ps(all=True)
        if services:
            names = [row.get("Name") or row.get("Service", "") for row in services]
            parts.append(Part("stack", "Remove the Docker Compose containers (MLflow, Dolt, telemetry)",
                              names, "", lambda: check(stack.down())))
        volumes = core_volumes(stack)
        if volumes:
            parts.append(Part("volumes", "Delete the data volumes", [r.name for r in volumes],
                              "deletes the Beads database (Dolt), MLflow runs/traces "
                              "and Grafana data; this cannot be undone",
                              lambda: remove_resources(volumes), destructive=True))
        if check_network():
            parts.append(Part("network", "Remove the Docker network", [NETWORK], "",
                              lambda: check(run(["docker", "network", "rm", NETWORK]))))
        images = built_images()
        if images:
            parts.append(Part("images", "Remove images built by setup (Scion, agents, Dolt)", images,
                              "rebuilding takes a while on the next ./setup.py",
                              lambda: check(run(["docker", "rmi", "-f", *images], timeout=600))))
    if SCION_HOME.exists():
        parts.append(Part("scion-home", f"Remove Scion's home folder {SCION_HOME}", [str(SCION_HOME)],
                          "deletes Scion settings, login, projects, agent workspaces and the Hub database",
                           remove_scion_home))
    cli_paths = [INSTALL_PATH, ROOT / "scion" / "build", ROOT / "scion" / "web" / "dist"]
    if is_shim(INSTALL_PATH):
        cli_paths.append(upstream_binary(INSTALL_PATH))
        cli_paths.extend(INSTALL_PATH.with_name(f"{INSTALL_PATH.name}-{extension}.sh")
                         for extension in ("local", "cross-project", "sessions"))
    cli = existing(cli_paths)
    if cli:
        parts.append(Part("cli", "Remove the installed scion CLI and build output", [str(p) for p in cli], "",
                          lambda: [remove(p) for p in cli]))
    config = existing([ROOT / ".env", ROOT / ".env.secrets"])
    if config:
        parts.append(Part("config", "Remove .env and .env.secrets", [str(p) for p in config],
                          "deletes your tokens and Dolt passwords", lambda: [remove(p) for p in config]))
    venv = existing([ROOT / ".venv"])
    if venv:
        parts.append(Part("venv", "Remove the Python environment (.venv)", [str(venv[0])], "",
                          lambda: remove(ROOT / ".venv")))
    return parts


def check_network() -> bool:
    return run_command(["docker", "network", "inspect", NETWORK], timeout=30).ok


def remove_tree(path: Path) -> None:
    """Delete a directory. Containers write root-owned files into it, so fall back to busybox."""
    try:
        shutil.rmtree(path)
    except PermissionError:
        result = run(["docker", "run", "--rm", "-v", f"{path}:/target", "busybox",
                      "find", "/target", "-mindepth", "1", "-delete"], timeout=600)
        if result.returncode != 0:
            raise
        shutil.rmtree(path)


def remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


# --- execute ---------------------------------------------------------------

def default_selection(parts: list[Part], *, volumes: bool) -> list[Part]:
    """Parts removed without asking: data volumes only with volumes=True."""
    return [p for p in parts if volumes or not p.destructive]


def execute(parts: list[Part], *, dry_run: bool = False, log: Callable[[str], None] = print) -> list[str]:
    """Remove the parts in order and return the keys that failed. Dry-run removes nothing."""
    failed = []
    for part in parts:
        if dry_run:
            continue
        log(f"[{part.key}] {part.title}")
        try:
            part.remove()
            log("    removed\n")
        except Exception as error:  # report and continue with the other parts
            log(f"    failed: {error}\n")
            failed.append(part.key)
    return failed


def describe(part: Part) -> None:
    print(f"[{part.key}] {part.title}")
    for item in part.items[:8]:
        print(f"    - {item}")
    if len(part.items) > 8:
        print(f"    ... and {len(part.items) - 8} more")
    if part.warning:
        print(f"    ! {part.warning}")
    print()


def choose_parts(parts: list[Part]) -> list[Part]:
    """Menu of the parts found. Data volumes and the Python environment are unticked by default."""
    for part in parts:
        describe(part)
    try:
        picked = ui.checkbox("What should be removed?",
                          [(part.title + (f"  (! {part.warning})" if part.warning else ""), part.key,
                            not part.destructive and part.key != "venv") for part in parts])
    except ImportError:  # questionary lives in .venv; stay usable without it
        picked = [p.key for p in parts if input(f"Remove '{p.title}'? (y/N): ").strip().lower().startswith("y")]
    return [part for part in parts if part.key in picked]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="only show what would be removed")
    parser.add_argument("--yes", action="store_true", help="remove the parts without a menu (keeps data volumes)")
    parser.add_argument("--volumes", action="store_true",
                        help="with --yes: also delete the data volumes (Beads, MLflow, Grafana)")
    parser.add_argument("--skip", default="", help="comma-separated parts to keep, e.g. config,images")
    args = parser.parse_args(argv)
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    interactive = sys.stdin.isatty()
    if not (args.dry_run or args.yes or interactive):
        parser.error("no terminal for the menu: use --yes (or --dry-run)")

    has_docker = docker_ok()
    if not has_docker:
        print("Note: Docker is not reachable, so containers, volumes and images are not cleaned up.\n"
              "      Start Docker and run ./cleanup.py again to remove them.\n")
    parts = [p for p in plan(has_docker=has_docker) if p.key not in skip]
    if not parts:
        print("Nothing to clean up.")
        return 0

    if args.dry_run:
        for part in parts:
            describe(part)
        print("Dry run: nothing was removed.")
        return 0
    if args.yes:
        chosen = default_selection(parts, volumes=args.volumes)
        for part in chosen:
            describe(part)
        kept = [p.title for p in parts if p not in chosen]
        if kept:
            print("Kept (add --volumes to delete the data volumes): " + "; ".join(kept) + "\n")
    else:
        chosen = choose_parts(parts)
        if not chosen:
            print("Nothing selected.")
            return 0
    failed = execute(chosen)
    if failed:
        print(f"Done, but these parts failed: {', '.join(failed)}. Run ./cleanup.py again.")
        return 1
    print(f"Removed {len(chosen)} of {len(parts)} part(s). Start fresh with: ./setup.py")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        sys.exit(130)
