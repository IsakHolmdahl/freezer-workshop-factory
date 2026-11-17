"""Apply the cross-project delegation settings from asf.yaml to the Hub.

Needs a running Scion. Projects are read from asf.local.yaml/asf.yaml unless
--project is given (repeatable). With --yes and no --project the current
selection is kept. Use scion cross-project setup for interactive selection.

    python scripts/steps/cross_project.py --dry-run
    python scripts/steps/cross_project.py --project a --project b --start
"""
import importlib.util
import sys

try:  # run as a script: put scripts/ on sys.path
    import _path  # noqa: F401
except ModuleNotFoundError:  # imported as steps.cross_project
    pass

from steps.common import Context, StepError, StepResult, cli


def _module():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "cross-project.py"
    spec = importlib.util.spec_from_file_location("cross_project", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(ctx: Context, **options) -> StepResult:
    if ctx.dry_run:
        ctx.log("would apply cross-project delegation policy to the Hub")
        return StepResult("dry-run", "cross-project: would apply")
    cp = _module()
    hub = ctx.adapter.service_endpoints().hub_human
    if not _hub_answers(hub):
        raise StepError(f"cross-project delegation needs a running Scion ({hub} does not answer)",
                        ["start Scion first: scripts/steps/scion_start.py",
                         "or tick 'Start Scion' in ./setup.py"])
    try:
        known = cp.projects()
        if len(known) < 2:
            return StepResult("skipped", f"only {len(known)} project on the Hub; nothing to connect")
        if options.get("projects"):
            _save_selection(cp, options["projects"], known)
        cp.apply(start=bool(options.get("start")))
    except cp.Failure as error:
        raise StepError(str(error), ["check the project slugs with: scion hub projects"]) from None
    return StepResult("done", "cross-project delegation applied")


def _save_selection(cp, slugs, known) -> None:
    import factory_env as fe

    unknown = sorted(set(slugs) - set(known))
    if unknown:
        raise cp.Failure(f"unknown project(s): {', '.join(unknown)}")
    peers = {slug: "coordinator" for slug in slugs}
    fe.set_local("cross_project.enabled", bool(peers))
    fe.set_local("cross_project.projects", peers)


def _hub_answers(url: str) -> bool:
    from steps.scion_start import hub_healthy

    return hub_healthy(url)


def add_arguments(parser):
    parser.add_argument("--project", action="append", dest="projects", metavar="SLUG",
                        help="project slug taking part (repeatable); saved to asf.local.yaml")
    parser.add_argument("--start", action="store_true",
                        help="start a receiving coordinator in selected projects that have none")


main = cli(run, add_arguments, __doc__)

if __name__ == "__main__":
    sys.exit(main())
