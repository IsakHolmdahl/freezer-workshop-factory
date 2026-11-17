"""Check the platform and the tools the setup needs, and initialise the Scion submodule.

Linux and macOS run natively. Windows runs inside WSL2: Scion's server and
Runtime Broker do not run on native Windows (scion/docs-site: hosted/user/windows.md).
Offers jq installation on Ubuntu/macOS; other tools are checked read-only.
Initialises the Scion submodule when the source is missing.
Every problem is a StepError that names the fix.

    python scripts/steps/prerequisites.py --dry-run
"""
import re
import sys
import platform
import subprocess

try:  # run as a script: put scripts/ on sys.path
    import _path  # noqa: F401
except ModuleNotFoundError:  # imported as steps.prerequisites: scripts/ is already on sys.path
    pass

import factory_env as fe
import scion_source
from steps import ui
from steps.common import Context, StepError, StepResult, cli

HINTS = {
    "docker": "Install Docker: https://docs.docker.com/engine/install/ (macOS: Docker Desktop; "
              "Windows: Docker Desktop with WSL integration enabled for this distribution)",
    "git": "Install git (Ubuntu/WSL: sudo apt install git, macOS: xcode-select --install)",
    "make": "Install make (Ubuntu/WSL: sudo apt install make, macOS: xcode-select --install)",
    "go": "Install Go >= 1.26: https://go.dev/doc/install",
    "npm": "Install Node.js 22 + npm: https://nodejs.org/en/download",
}
MIN_GO = (1, 26)
MIN_COMPOSE = (2, 24)


def is_wsl() -> bool:
    return sys.platform.startswith("linux") and "microsoft" in platform.uname().release.lower()


def check_platform() -> None:
    """Native Windows cannot run the Hub or the broker; a checkout on a Windows drive breaks under WSL."""
    if sys.platform in ("win32", "cygwin", "msys"):
        raise StepError("Scion's Hub and Runtime Broker do not run on native Windows",
                        ["run .\\setup.ps1 in PowerShell: it checks WSL2 and Docker Desktop and starts setup in WSL",
                         "or open your WSL2 distribution, clone this repository under ~ and run: make setup"])
    if is_wsl() and str(fe.ROOT).startswith("/mnt/"):
        raise StepError(f"this checkout is on a Windows drive ({fe.ROOT})",
                        ["clone it inside the WSL filesystem instead (for example ~/ai-software-factory-opencode): "
                         "file permissions, symlinks and Docker bind mounts are unreliable under /mnt/<drive>"])


def jq_install_command():
    if sys.platform == "darwin":
        return ["brew", "install", "jq"]
    if sys.platform.startswith("linux"):
        try:
            if platform.freedesktop_os_release().get("ID") == "ubuntu":
                return ["sudo", "apt", "install", "-y", "jq"]
        except OSError:
            pass
    return None


def ensure_jq(ctx):
    if fe.which("jq"):
        return
    command = jq_install_command()
    hint = " ".join(command) if command else "install jq using your system package manager: https://jqlang.org/download/"
    ui.info("The Scion shell extensions require jq for JSON handling.")
    ui.info(f"Install jq: {hint}")
    if ctx.dry_run:
        ctx.log(f"would prompt to install jq: {hint}")
        return
    if ctx.assume_yes:
        raise StepError("jq is missing", [hint, "rerun setup after installing jq"])
    if command:
        if not ui.confirm(f"Run {' '.join(command)} now?", default=True):
            raise StepError("jq installation deferred", [hint, "rerun setup after installing jq"])
        if not fe.which(command[0]):
            raise StepError(f"{command[0]} is not available", [hint, "install jq manually and rerun setup"])
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            raise StepError("jq installation failed", [hint, "rerun setup after installing jq"])
    else:
        ui.ask("Install jq in another terminal, then press Enter to continue")
    if not fe.which("jq"):
        raise StepError("jq is still missing from PATH", [hint, "open a new terminal and rerun setup"])


def _version(text: str) -> tuple[int, int] | None:
    match = re.search(r"(\d+)\.(\d+)", text)
    return (int(match.group(1)), int(match.group(2))) if match else None


def check_tools() -> None:
    missing = [tool for tool in HINTS if not fe.which(tool)]
    if missing:
        raise StepError(f"missing tools: {', '.join(missing)}",
                        [HINTS[tool] for tool in missing] + ["open a NEW terminal so the PATH is updated"])
    if fe.which("go"):
        found = _version(fe.run(["go", "version"]).stdout)
        if found is None or found < MIN_GO:
            raise StepError(f"Go {MIN_GO[0]}.{MIN_GO[1]} or newer is required", [HINTS["go"]])


def check_docker() -> None:
    info = fe.run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=20)
    if info.returncode != 0:
        if "permission denied" in info.stderr.lower():
            raise StepError("permission denied on the Docker socket",
                            ["sudo usermod -aG docker $USER",
                             "then log out and back in (or run: newgrp docker)"])
        fix = ["sudo systemctl start docker (macOS: open Docker Desktop)"]
        if is_wsl():
            fix = ["start Docker Desktop on Windows and enable Settings > Resources > WSL integration "
                   "for this distribution", "or install Docker Engine inside WSL: https://docs.docker.com/engine/install/"]
        raise StepError(f"Docker is not reachable: {info.stderr.strip()[:200]}", fix)
    compose = fe.run(["docker", "compose", "version", "--short"])
    version = _version(compose.stdout) if compose.returncode == 0 else None
    if version is None:
        raise StepError("docker compose v2 is missing",
                        ["install the Compose v2 plugin: https://docs.docker.com/compose/install/"])
    if version < MIN_COMPOSE:
        raise StepError(f"Docker Compose {compose.stdout.strip()} is too old; "
                        f"{MIN_COMPOSE[0]}.{MIN_COMPOSE[1]} or newer is required",
                        ["upgrade the Compose v2 plugin: https://docs.docker.com/compose/install/"])


def run(ctx: Context, **options) -> StepResult:
    check_platform()
    ensure_jq(ctx)
    check_tools()
    check_docker()
    if ctx.dry_run:
        ctx.log("would run: git submodule update --init scion")
        scion_source.ensure_source(dry_run=True)
        return StepResult("dry-run", "prerequisites: would select configured Scion source")
    try:
        changed = scion_source.ensure_source()
    except RuntimeError as error:
        raise StepError(str(error), ["check SCION_SOURCE_REF in .env and access to the Scion remote"]) from None
    return StepResult("done" if changed else "skipped", f"Scion source: {scion_source.source_ref()}")


main = cli(run, None, __doc__, needs_adapter=False)

if __name__ == "__main__":
    sys.exit(main())
