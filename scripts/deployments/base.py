"""Adapter contract shared by setup, doctor, cleanup and the sync scripts.

An adapter owns where a deployment runs and how the Scion CLI reaches it.
Callers never build `docker compose` argv or rewrite commands themselves.
Dry-run is enforced here: mutating commands are logged and not executed.
"""
import shlex
import subprocess
import queue
import threading
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, ClassVar, Iterator, Sequence

import factory_env as fe

# Check statuses (same values doctor.py has always used).
OK, WARN, FAIL, SKIP = "ok", "warn", "FAIL", "skip"


@dataclass
class CommandResult:
    """Outcome of one command. Never raises for a non-zero exit, a missing binary or a timeout."""
    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass
class Check:
    status: str                       # OK | WARN | FAIL | SKIP
    title: str
    detail: str = ""
    fix: list[str] = field(default_factory=list)
    autofix: Callable[[], str] | None = None


@dataclass
class Endpoints:
    hub_human: str                    # URL a person opens
    hub_agent: str                    # URL agents and brokers use
    broker: str | None
    mlflow: str
    dolt: str


@dataclass
class Resource:
    kind: str                         # container | volume | network | image | path
    name: str
    destructive: bool
    remove: Callable[[], None]        # volumes: destructive=True, removed only when selected


@dataclass
class DeploymentStatus:
    running: bool
    healthy: bool
    detail: str = ""


@dataclass
class ImageStatus:
    ref: str
    present: bool
    action: str                       # "ok" | "pulled" | "missing"


class DeployError(Exception):
    """User-facing failure with the commands that fix it."""

    def __init__(self, message: str, fix: Sequence[str] = ()):
        super().__init__(message)
        self.message = message
        self.fix = list(fix)


# Scion CLI invocations that only read state. Everything else is treated as a write
# and is only logged in dry-run. Leading global flags (e.g. --non-interactive) are ignored.
READ_ONLY_SCION = (("version",), ("list",), ("hub", "list"), ("server", "status"), ("hub", "env", "get"))


def is_read_only_scion(args: Sequence[str]) -> bool:
    words = [a for a in args if not a.startswith("--")]
    return any(tuple(words[:len(verb)]) == verb for verb in READ_ONLY_SCION)


def run_command(argv, *, timeout: int = 60, input: str | None = None, env: dict | None = None,
                mutating: bool = False, dry_run: bool = False, log: Callable[[str], None] = print,
                capture: bool = True) -> CommandResult:
    """Run argv in the repository root. Mutating commands are only logged in dry-run."""
    argv = [str(a) for a in argv]
    if mutating and dry_run:
        log("$ " + shlex.join(argv))
        return CommandResult(argv, 0)
    if capture and any(Path(arg).name == "sync-factory.py" for arg in argv):
        return run_publish_command(argv, timeout=timeout, input=input, env=env, log=log)
    # Captured builds/pulls otherwise produce no terminal output until completion.
    # Show the executable/operation only: argv can contain secret values.
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
    console = Console(highlight=False)
    progress = Progress(SpinnerColumn(), TextColumn("{task.description}"), TimeElapsedColumn(),
                        console=console, transient=True, disable=not capture or not console.is_terminal)
    try:
        with progress:
            progress.add_task(f"Running {Path(argv[0]).name} — waiting for completion", total=None)
            completed = subprocess.run(argv, capture_output=capture, text=True, timeout=timeout,
                                       input=input, env=env, cwd=fe.ROOT)
    except FileNotFoundError as error:
        return CommandResult(argv, 127, "", str(error))
    except subprocess.TimeoutExpired:
        return CommandResult(argv, 124, "", f"timed out after {timeout}s")
    return CommandResult(argv, completed.returncode, completed.stdout or "", completed.stderr or "")


def run_publish_command(argv, *, timeout, input=None, env=None, log=print):
    """Stream only item progress, retaining full merged output for diagnostics."""
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                                   text=True, env=env, cwd=fe.ROOT)
    except FileNotFoundError as error:
        return CommandResult(argv, 127, "", str(error))
    lines = queue.Queue()
    output = []
    def read_output():
        try:
            for line in process.stdout:
                lines.put(line)
        finally:
            lines.put(None)
    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
    console = Console(highlight=False)
    progress = Progress(SpinnerColumn(), TextColumn("{task.description}"), TimeElapsedColumn(),
                        console=console, transient=True, disable=not console.is_terminal)
    task = progress.add_task("Preparing publish inventory", total=None)
    progress.start()
    try:
        if input is not None:
            process.stdin.write(input)
            process.stdin.close()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            try:
                line = lines.get(timeout=min(remaining, 0.2))
            except queue.Empty:
                continue
            if line is None:
                break
            output.append(line)
            if line.startswith("Publish ["):
                progress.update(task, description=line.rstrip())
                log(line.rstrip())
        code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        return CommandResult(argv, code, "".join(output))
    except subprocess.TimeoutExpired:
        return CommandResult(argv, 124, "".join(output), f"publishing timed out after {timeout}s")
    finally:
        progress.stop()
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


class Adapter(ABC):
    name: ClassVar[str]               # "local" | "compose" | "containers"

    def __init__(self, cfg: dict, *, dry_run: bool = False, log: Callable[[str], None] = print):
        self.cfg = cfg
        self.dry_run = dry_run
        self.log = log

    def _run(self, argv, *, mutating: bool = False, timeout: int = 60, input: str | None = None,
             env: dict | None = None) -> CommandResult:
        """Run a host command; `mutating` commands are only logged in dry-run."""
        return run_command(argv, timeout=timeout, input=input, env=env, mutating=mutating,
                           dry_run=self.dry_run, log=self.log)

    # lifecycle (idempotent: start on a running, healthy deployment is a no-op)
    @abstractmethod
    def start(self, *, build: bool = True) -> DeploymentStatus: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def status(self) -> DeploymentStatus: ...

    # execution
    @abstractmethod
    def scion(self, args: Sequence[str], *, input: str | None = None, interactive: bool = False,
              timeout: int = 60) -> CommandResult:
        """Run the Scion CLI against this deployment. Secrets go through `input` (stdin), never argv."""

    @abstractmethod
    def run_repo_script(self, script: str, args: Sequence[str]) -> CommandResult:
        """Run scripts/<script> where the Scion CLI lives (host, or inside the container at /asf)."""

    @contextmanager
    def stage_bundle(self, path: Path) -> Iterator[Path]:
        """Yield `path` as the CLI execution environment sees it. Default: same path."""
        yield Path(path)

    def prepare_publish(self) -> None:
        """Hook before publishing catalog content. Default: nothing to do."""

    # images / settings / endpoints
    def ensure_images(self, images: Sequence[str]) -> list[ImageStatus]:
        """Check images in the host Docker engine; pull missing ones. No import step."""
        statuses = []
        for ref in images:
            if self._run(["docker", "image", "inspect", ref], timeout=30).ok:
                statuses.append(ImageStatus(ref, True, "ok"))
            elif self._run(["docker", "pull", ref], mutating=True, timeout=600).ok:
                statuses.append(ImageStatus(ref, True, "pulled"))
            else:
                statuses.append(ImageStatus(ref, False, "missing"))
        return statuses

    @abstractmethod
    def settings_path(self) -> Path: ...

    @abstractmethod
    def render_settings(self, current: dict) -> dict:
        """Pure: merge `current` settings with this deployment's policy and return the new dict."""

    @abstractmethod
    def service_endpoints(self) -> Endpoints: ...

    # doctor / cleanup
    @abstractmethod
    def health_checks(self) -> list[Check]:
        """Adapter-specific checks only; common checks live in doctor.py."""

    @abstractmethod
    def resources(self) -> list[Resource]:
        """Deployment-owned resources only. Volumes are destructive and removed only when selected."""
