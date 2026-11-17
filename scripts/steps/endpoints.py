"""Check that each agent gateway model answers with one small model call.

For the MLflow gateway the bootstrap script is run in the MLflow container first
(it creates the endpoints and applies the provider key from .env.secrets).
A failing endpoint lists its fixes and asks before setup continues (with --yes it
continues without asking).

    python scripts/steps/endpoints.py --dry-run
    python scripts/steps/endpoints.py --yes
"""
import sys

try:  # run as a script: put scripts/ on sys.path
    import _path  # noqa: F401
except ModuleNotFoundError:  # imported as steps.endpoints
    pass

import doctor
import factory_env as fe
from steps import ui
from steps.common import Context, StepError, StepResult, cli

MLFLOW_CONTAINER = "mlflow-server"
SEED_SCRIPT = "/opt/mlflow-bootstrap/seed_gateway.py"


def run(ctx: Context, **options) -> StepResult:
    if ctx.dry_run:
        ctx.log("would probe each agent gateway model with one small chat completion")
        return StepResult("dry-run", "endpoints: would probe the gateway models")
    ui.heading(f"Agent gateway models ({', '.join(fe.gateway_models())})")
    if fe.gateway_type() == "mlflow":
        with ui.activity("Preparing MLflow model endpoints"):
            seeded = fe.run(["docker", "exec", MLFLOW_CONTAINER, "python", SEED_SCRIPT], timeout=120)
        if seeded.returncode:
            ui.info((seeded.stderr or seeded.stdout).strip()[-300:])
    failed = []
    for name in fe.gateway_models():
        with ui.activity(f"Checking model endpoint: {name}"):
            result = doctor.check_inference(name)
        ui.info(f"{name}: {result.detail}")
        if result.status != doctor.OK:
            failed.append((name, result))
    for name, result in failed:
        ui.info(f"Endpoint {name} does not answer. Options:")
        for fix in result.fix:
            ui.info(f"  - {fix}")
    if not failed:
        return StepResult("done", "gateway endpoints answer")
    # --yes never asks: the endpoints are reported and setup goes on.
    if not ctx.assume_yes and not ui.confirm("Continue anyway?", default=False):
        raise StepError("gateway model endpoints do not answer",
                        ["fix the endpoints above, then run this step again"])
    return StepResult("done", f"{len(failed)} gateway endpoint(s) do not answer; continued")


main = cli(run, None, __doc__, needs_adapter=False)

if __name__ == "__main__":
    sys.exit(main())
