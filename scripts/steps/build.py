"""Build the agent images and the Scion CLI, Hub and web UI from the submodule.

Both run scripts/sync-factory.py with the flags setup.py always used.
Target: `images` (agent images), `scion` (Scion binaries). No target does both.

    python scripts/steps/build.py images --dry-run
    python scripts/steps/build.py scion --install-path ~/.local/bin/scion
"""
import os
import subprocess
import sys

try:  # run as a script: put scripts/ on sys.path
    import _path  # noqa: F401
except ModuleNotFoundError:  # imported as steps.build: scripts/ is already on sys.path
    pass

import factory_env as fe
from steps import ui
from steps.common import Context, StepError, StepResult, cli

# Flags shared by both builds: harness configs are installed later, once the Hub runs.
SYNC_ONLY = ["--skip-skills", "--skip-templates", "--skip-harness-install", "--dolt-url", ""]


def _sync_argv(extra: list[str]) -> list[str]:
    return [sys.executable, str(fe.SCRIPTS / "sync-factory.py"), *SYNC_ONLY, *extra]


def run_images(ctx: Context) -> StepResult:
    ui.heading("Build the agent images (base image, asf-opencode and tool variants)")
    argv = _sync_argv(["--skip-binary"])
    if ctx.dry_run:
        ctx.log(f"would run: {' '.join(map(str, argv))}")
        return StepResult("dry-run", "agent images: would build")
    _run(argv)
    return StepResult("done", "agent images built")


def run_scion(ctx: Context, install_path: str | None = None) -> StepResult:
    ui.heading("Build Scion (CLI, Hub and web UI)")
    target = str(install_path or fe.install_path())
    argv = _sync_argv(["--skip-build", "--install-path", target])
    if ctx.dry_run:
        ctx.log(f"would run: {' '.join(map(str, argv))}")
        return StepResult("dry-run", f"Scion: would build into {target}")
    _run(argv, env={**os.environ, "INSTALL_PATH": target})
    return StepResult("done", f"Scion built into {target}")


def _run(argv: list[str], env: dict | None = None) -> None:
    """Output streams to the terminal; a non-zero exit becomes a StepError."""
    ui.info(f"$ {' '.join(map(str, argv))}")
    completed = subprocess.run([str(a) for a in argv], cwd=fe.ROOT, env=env)
    if completed.returncode != 0:
        raise StepError(f"build command failed (exit {completed.returncode})",
                        ["read the output above for the failing step, fix it, and run this step again"])


def run(ctx: Context, **options) -> StepResult:
    target = options.get("target")
    install_path = options.get("install_path")
    if target == "images":
        return run_images(ctx)
    if target == "scion":
        return run_scion(ctx, install_path)
    images = run_images(ctx)
    scion = run_scion(ctx, install_path)
    return StepResult(images.status, f"{images.message}; {scion.message}")


def add_arguments(parser):
    parser.add_argument("--install-path", help="where the scion binary is installed (default: scion.install_path)")
    parser.add_argument("target", nargs="?", choices=["images", "scion"],
                        help="build only the agent images or only Scion (default: both)")


main = cli(run, add_arguments, __doc__, needs_adapter=False)

if __name__ == "__main__":
    sys.exit(main())
