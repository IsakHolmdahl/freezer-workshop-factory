"""Enable rootless Docker for agent Testcontainers tests (wraps testcontainers-docker.py).

Idempotent: an already enabled and mounted socket is skipped.

    python scripts/steps/testcontainers.py --dry-run
    python scripts/steps/testcontainers.py --yes
"""
import importlib.util
import sys

try:  # run as a script: put scripts/ on sys.path
    import _path  # noqa: F401
except ModuleNotFoundError:  # imported as steps.testcontainers
    pass

from steps.common import Context, StepError, StepResult, cli

SCRIPT = "testcontainers-docker.py"


def _module():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / SCRIPT
    spec = importlib.util.spec_from_file_location("testcontainers_docker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(ctx: Context, **options) -> StepResult:
    tc = _module()
    if ctx.dry_run:
        ctx.log("would enable rootless Docker for Testcontainers (user-level dockerd, agent socket mount)")
        return StepResult("dry-run", "testcontainers: would enable rootless Docker")
    if _already_enabled(tc):
        return StepResult("skipped", "rootless Docker for Testcontainers already enabled")
    try:
        tc.enable(interactive=not ctx.assume_yes)
    except tc.Failure as error:
        raise StepError(str(error), ["run: ./setup.py --doctor for the current state",
                                     "then re-run: scripts/steps/testcontainers.py"]) from None
    return StepResult("done", "rootless Docker for Testcontainers enabled")


def _already_enabled(tc) -> bool:
    import factory_env as fe
    import scion_settings

    sock = scion_settings.rootless_docker_socket()
    return (bool(fe.get("testcontainers.enabled", True)) and sock.is_socket()
            and scion_settings.testcontainers_volume(sock) is not None)


main = cli(run, None, __doc__)

if __name__ == "__main__":
    sys.exit(main())
