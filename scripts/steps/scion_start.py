"""Start Scion through the active deployment and wait until the Hub answers.

Idempotent: a running, healthy Hub is skipped. Reports the Runtime Broker state
but does not fail on it (the doctor reports it).

    python scripts/steps/scion_start.py --dry-run
"""
import sys
import time
import urllib.error
import urllib.request

try:  # run as a script: put scripts/ on sys.path
    import _path  # noqa: F401
except ModuleNotFoundError:  # imported as steps.scion_start
    pass

from steps.common import Context, StepError, StepResult, cli

HUB_TIMEOUT = 180


def hub_healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/healthz", timeout=5) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def wait_for(probe, timeout: int, interval: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if probe():
            return True
        time.sleep(interval)
    return probe()


def run(ctx: Context, **options) -> StepResult:
    hub = ctx.adapter.service_endpoints().hub_human
    before = ctx.adapter.status()
    if before.running and before.healthy and hub_healthy(hub):
        return StepResult("skipped", f"Scion already running ({hub})")
    if ctx.dry_run:
        ctx.log(f"would start Scion through the {ctx.adapter.name} deployment and wait for {hub}/healthz")
        return StepResult("dry-run", "scion: would start")
    after = ctx.adapter.start(build=options.get("build", True))
    if not after.healthy or not wait_for(lambda: hub_healthy(hub), HUB_TIMEOUT):
        raise StepError(f"Scion did not become healthy at {hub}: {after.detail}",
                        [f"check the logs of the {ctx.adapter.name} deployment",
                         "./setup.py --doctor shows the state of each service"])
    return StepResult("done", f"Scion running at {hub}")


def add_arguments(parser):
    parser.add_argument("--no-build", dest="build", action="store_false",
                        help="do not build images before starting")


main = cli(run, add_arguments, __doc__)

if __name__ == "__main__":
    sys.exit(main())
