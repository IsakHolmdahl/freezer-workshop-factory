"""Start the core services (MLflow, Dolt, telemetry) with compose.yaml, then wait until healthy.

The Scion Hub is not started here (scion_start does that). Core services run in every
deployment, so this step does not depend on the adapter. Idempotent: when all core
services are already running and healthy the step is skipped, unless --force asks
Compose to apply changed images, services and settings (`make start`).

    python scripts/steps/services.py --dry-run
    python scripts/steps/services.py --no-build
    python scripts/steps/services.py --force
"""
import sys

try:  # run as a script: put scripts/ on sys.path
    import _path  # noqa: F401
except ModuleNotFoundError:  # imported as steps.services: scripts/ is already on sys.path
    pass

from deployments.stack import core_healthy, start_core
from steps.common import Context, StepError, StepResult, cli


def run(ctx: Context, **options) -> StepResult:
    build = options.get("build", True)
    if ctx.dry_run:
        start_core(ctx.cfg, build=build, dry_run=True, log=ctx.log)
        return StepResult("dry-run", "services: would start MLflow, Dolt and telemetry")
    if not options.get("force") and core_healthy(ctx.cfg):
        return StepResult("skipped", "core services already running and healthy")
    start_core(ctx.cfg, build=build, log=ctx.log)
    if not core_healthy(ctx.cfg):
        raise StepError("core services did not become healthy",
                        ["docker compose -f compose.yaml ps", "docker compose -f compose.yaml logs --tail 50"])
    return StepResult("done", "core services running and healthy")


def add_arguments(parser):
    parser.add_argument("--no-build", dest="build", action="store_false",
                        help="do not build images before starting")
    parser.add_argument("--force", action="store_true",
                        help="run compose up even when everything is healthy, to apply changes")


main = cli(run, add_arguments, __doc__, needs_adapter=False)

if __name__ == "__main__":
    sys.exit(main())
