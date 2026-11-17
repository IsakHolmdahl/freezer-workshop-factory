"""The only place that builds `docker compose` argv.

A ComposeStack is one Compose project: files, profiles and environment. Read-only
queries always run; mutating commands are logged and skipped in dry-run.
"""
import json
import os
import shlex
from pathlib import Path
from typing import Mapping, Sequence

import factory_env as fe
from .base import CommandResult, DeployError, run_command

COMPOSE_FILE = fe.ROOT / "compose.yaml"
MIN_COMPOSE_VERSION = (2, 24)


class ComposeStack:
    def __init__(self, files: Sequence[Path], *, profiles: Sequence[str] = (),
                 project_name: str | None = None, env: Mapping[str, str] | None = None,
                 dry_run: bool = False, log=print):
        self.files = [Path(f) for f in files]
        self.profiles = list(profiles)
        self.project_name = project_name
        self.env = dict(env) if env is not None else None
        self.dry_run = dry_run
        self.log = log

    def argv(self, *args: str) -> list[str]:
        argv = ["docker", "compose"]
        if self.project_name:
            argv += ["--project-name", self.project_name]
        for file in self.files:
            argv += ["-f", str(file)]
        for profile in self.profiles:
            argv += ["--profile", profile]
        return argv + [str(a) for a in args]

    def run(self, *args: str, mutating: bool, timeout: int = 60, input: str | None = None,
            env: Mapping[str, str] | None = None) -> CommandResult:
        merged = {**(self.env or {}), **(env or {})}
        return run_command(self.argv(*args), timeout=timeout, input=input,
                           env={**os.environ, **merged} if merged else None, mutating=mutating,
                           dry_run=self.dry_run, log=self.log)

    def services(self) -> list[str]:
        result = self.run("config", "--services", mutating=False)
        return result.stdout.split() if result.ok else []

    def up(self, services: Sequence[str] = (), *, build: bool = True, wait: bool = False) -> CommandResult:
        args = ["up", "-d"]
        if build:
            args.append("--build")
        if wait:
            args.append("--wait")
        return self.run(*args, *services, mutating=True, timeout=900)

    def stop(self, *services: str) -> CommandResult:
        return self.run("stop", *services, mutating=True, timeout=300)

    def down(self, *, volumes: bool = False) -> CommandResult:
        args = ["down", "--volumes"] if volumes else ["down"]
        return self.run(*args, mutating=True, timeout=300)

    def ps(self, *, all: bool = False) -> list[dict]:
        args = ["ps", "--format", "json"] + (["--all"] if all else [])
        result = self.run(*args, mutating=False, timeout=30)
        if not result.ok or not result.stdout.strip():
            return []
        text = result.stdout.strip()
        # Compose < 2.21 prints one JSON object per line; newer versions print an array.
        if text.startswith("["):
            return json.loads(text)
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def exec(self, service: str, cmd: Sequence[str], *, input: str | None = None,
             workdir: str | None = None, mutating: bool = False, timeout: int = 60) -> CommandResult:
        args = ["exec", "-T"] + (["-w", workdir] if workdir else []) + [service, *cmd]
        return self.run(*args, mutating=mutating, timeout=timeout, input=input)

    def project(self) -> str:
        result = self.run("config", "--format", "json", mutating=False)
        try:
            return str(json.loads(result.stdout).get("name") or "")
        except ValueError:
            return ""

    def volume(self, name: str) -> str | None:
        """Full name of this project's volume (e.g. asf_dolt-data), or None if it does not exist."""
        project = self.project()
        if not project:
            return None
        full = f"{project}_{name}"
        ok = run_command(["docker", "volume", "inspect", full], timeout=30).ok
        return full if ok else None

    def version(self) -> tuple[int, ...] | None:
        result = run_command(["docker", "compose", "version", "--short"], timeout=20)
        if not result.ok:
            return None
        parts = []
        for piece in result.stdout.strip().lstrip("v").split("."):
            digits = "".join(ch for ch in piece if ch.isdigit())
            if not digits:
                break
            parts.append(int(digits))
        return tuple(parts) or None


SCION_SERVICE = "scion"   # the Hub/web UI service; started by the adapter, not by core_stack


def core_stack(cfg, **kwargs) -> ComposeStack:
    """Core infrastructure stack (MLflow, Dolt, telemetry); Scion runs on the host."""
    return ComposeStack([COMPOSE_FILE], **kwargs)


def one_shot_services(stack: ComposeStack) -> set[str]:
    """Services with an explicit `restart: "no"`: jobs that are expected to exit after one run."""
    result = stack.run("config", "--format", "json", mutating=False)
    try:
        services = json.loads(result.stdout).get("services") or {}
    except ValueError:
        return set()
    return {name for name, spec in services.items()
            if str((spec or {}).get("restart")).lower() in ("no", "false")}


def core_healthy(cfg, **kwargs) -> bool:
    """True when every core service (all but the Hub) is healthy.

    Long-running services must be running and healthy. One-shot services (restart: "no",
    e.g. create-bucket) must have exited with code 0; a non-zero exit is not healthy.
    """
    stack = core_stack(cfg, **kwargs)
    services = {s for s in stack.services() if s != SCION_SERVICE}
    one_shot = one_shot_services(stack)
    rows = {row.get("Service"): row for row in stack.ps(all=True)}

    def healthy(name: str) -> bool:
        row = rows.get(name, {})
        if name in one_shot:
            return row.get("State") == "exited" and row.get("ExitCode") == 0
        return row.get("State") == "running" and row.get("Health", "") in ("", "healthy")

    return bool(services) and all(healthy(name) for name in services)


def start_core(cfg, *, build: bool = True, **kwargs) -> None:
    """Create the external network if missing, then start and wait for the core services.

    Raises DeployError when the stack cannot start; health is checked by the caller.
    """
    stack = core_stack(cfg, **kwargs)
    network = fe.COMPOSE_NETWORK
    if not run_command(["docker", "network", "inspect", network], timeout=30).ok:
        created = run_command(["docker", "network", "create", network], timeout=60, mutating=True,
                              dry_run=stack.dry_run, log=stack.log)
        if not created.ok:
            raise DeployError(f"could not create the Docker network {network}", fix=[
                f"docker network create {network}"])
    services = [s for s in stack.services() if s != SCION_SERVICE]
    if not services and stack.dry_run:
        stack.log("would start the core services (compose.yaml could not be read in dry-run)")
        return
    if not services:
        raise DeployError("compose.yaml lists no core services", fix=[shlex.join(stack.argv("config", "--services"))])
    result = stack.up(services, build=build, wait=True)
    if not result.ok:
        detail = result.stderr.strip() or result.stdout.strip()
        message = "the core services (MLflow, Dolt, telemetry) did not start"
        if detail:
            message += "\n" + detail
        raise DeployError(message, fix=[
            shlex.join(stack.argv("logs", "--tail", "50")), shlex.join(stack.argv("up", "-d", "--wait"))])


def check_version(version: tuple[int, ...] | None) -> None:
    """Raise DeployError unless Compose is at least MIN_COMPOSE_VERSION."""
    wanted = ".".join(map(str, MIN_COMPOSE_VERSION))
    if version is None:
        raise DeployError("Docker Compose is not available", fix=[
            "Install the compose plugin: https://docs.docker.com/compose/install/",
        ])
    if version[:len(MIN_COMPOSE_VERSION)] < MIN_COMPOSE_VERSION:
        found = ".".join(map(str, version))
        raise DeployError(f"Docker Compose {found} is too old; {wanted} or newer is required", fix=[
            "Upgrade the Docker Compose plugin: https://docs.docker.com/compose/install/",
        ])
