"""Guided installer for the AI Software Factory (MLflow, Beads/Dolt, Scion with OpenCode agents).

Runs on Linux, macOS and Windows (inside WSL2; start with setup.ps1 there).

The installer is a thin orchestration over the standalone steps in scripts/steps/.
Each step is idempotent: run it again at any time, finished steps are skipped.
Every step can also run on its own (see --list-steps).

    ./setup.py                       # menu: choose which tasks to run
    ./setup.py --yes                 # everything, accept defaults, no menu
    ./setup.py --only build_scion,services # exactly these steps, in the order listed
    ./setup.py --skip build_images   # everything except these steps
    ./setup.py --dry-run             # show what would change; change nothing
    ./setup.py --list-steps          # step names and what they do
    ./setup.py --doctor [--fix]      # only check the installation
"""
import argparse
import importlib
import sys
from dataclasses import dataclass

try:  # run as a script: put scripts/ on sys.path
    import _path  # noqa: F401
except ModuleNotFoundError:  # imported as scripts.setup or from a test
    pass

import doctor
import factory_env as fe
from deployments.base import DeployError
from rich.markup import escape
from steps import ui
from steps.common import Context, StepError


@dataclass(frozen=True)
class Step:
    """One setup step: the module under scripts/steps/, its menu task and the options it gets."""
    name: str
    module: str
    summary: str
    task: str | None = None           # menu task that selects it; None = always runs
    options: tuple = ()               # (key, value) pairs passed to run()
    needs_adapter: bool = True


STEPS: dict[str, Step] = {step.name: step for step in [
    Step("prerequisites", "prerequisites", "tools, Docker, Compose >= 2.24, Scion submodule", needs_adapter=False),
    Step("configuration", "configuration", ".env, secrets, model provider key, admin identity", needs_adapter=False),
    Step("services", "services", "start MLflow, Dolt and telemetry", task="services",
         needs_adapter=False),
    Step("endpoints", "endpoints", "check the agent gateway model endpoints", task="services",
         needs_adapter=False),
    Step("scion_cli", "scion_cli", "choose Scion install location and offer duplicate cleanup", task="scion-build",
         needs_adapter=False),
    Step("build_scion", "build", "build Scion (CLI, Hub, web UI)", task="scion-build",
         options=(("target", "scion"),), needs_adapter=False),
    Step("build_images", "build", "build the agent images", task="images",
         options=(("target", "images"),), needs_adapter=False),
    Step("testcontainers", "testcontainers", "rootless Docker for Testcontainers (experimental)", task="testcontainers"),
    Step("scion_settings", "scion_settings_step", "write Scion settings (~/.scion/settings.yaml)", task="start"),
    Step("scion_start", "scion_start", "start Scion (Hub, broker, web UI)", task="start"),
    Step("secrets", "secrets", "optional GitHub token stored directly in the Hub", task="publish"),
    Step("hub_wiring", "hub_wiring", "publish roles, skills, Hub env and secrets", task="publish"),
    Step("doctor", "doctor_step", "final check of the installation"),
]}

# Menu tasks (what the checkbox offers); each maps onto steps above.
TASKS = {
    "services": "MLflow and Beads: start services, check the model endpoints",
    "scion-build": "Build Scion: CLI, Hub and web UI (slow)",
    "images": "Build the agent images (slow)",
    "testcontainers": "Rootless Docker for Testcontainers integration tests (experimental)",
    "start": "Start Scion (settings, Hub, broker, web UI)",
    "publish": "Publish roles, skills and Hub settings (Scion must be running)",
}
# Not preselected: experimental rootless Docker.
OPTIONAL_TASKS = {"testcontainers"}
# Always run first: quick, and only ask what is still missing.
PREREQUISITE_STEPS = ["prerequisites", "configuration"]
FINAL_STEP = "doctor"


def choose_tasks() -> list[str]:
    picked = ui.checkbox("What should setup do?",
                      [(title, key, key not in OPTIONAL_TASKS) for key, title in TASKS.items()])
    return [key for key in TASKS if key in picked]


def steps_for_tasks(tasks) -> list[str]:
    """Step names for the chosen menu tasks, in STEPS order, with prerequisites first and the final check last."""
    return [name for name, step in STEPS.items() if step.task is None or step.task in tasks]


def _module(step: Step):
    return importlib.import_module(f"steps.{step.module}")


def run_steps(names: list[str], *, dry_run: bool = False, assume_yes: bool = False,
              interactive: bool = False) -> int:
    """Run the named steps in order; stop at the first StepError. Returns 0, or 1 when a step failed."""
    ui.set_assume_yes(assume_yes)
    adapter_ctx = None
    for name in names:
        step = STEPS[name]
        args = argparse.Namespace(dry_run=dry_run, yes=assume_yes)
        options = dict(step.options)
        if name == FINAL_STEP:
            options["quick"] = True
        try:
            if step.needs_adapter:
                adapter_ctx = adapter_ctx or Context.from_args(args, needs_adapter=True)
                ctx = adapter_ctx
            else:
                ctx = Context.from_args(args, needs_adapter=False)
            result = _module(step).run(ctx, **options)
        except (StepError, DeployError) as error:
            ui.console.print(f"\n[bold red]✘ Setup stopped at {name}:[/] {escape(error.message)}")
            for line in error.fix:
                print(f"  try: {line}")
            print("Fix the problem and run ./setup.py again; finished steps are skipped.")
            return 1
        ui.info(f"{result.status}: {result.message}")
    if not dry_run and FINAL_STEP in names:
        ui.console.print("\n[bold green]✔ All set.[/]")
        ui.info(f"Scion web UI:  {fe.hub_url()}")
        ui.info(f"MLflow:        {fe.mlflow_url()}")
        ui.info("Check later:   ./setup.py --doctor")
    return 0


def _names(value: str) -> list[str]:
    names = [part.strip() for part in value.split(",") if part.strip()]
    unknown = [name for name in names if name not in STEPS]
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown step(s): {', '.join(unknown)} (see --list-steps)")
    return names


def list_steps() -> int:
    for name, step in STEPS.items():
        task = f" [menu: {step.task}]" if step.task else ""
        print(f"{name:<16} {step.summary}{task}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], epilog=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--doctor", action="store_true", help="only check the installation")
    parser.add_argument("--fix", action="store_true", help="with --doctor: apply the safe automatic repairs")
    parser.add_argument("--yes", action="store_true", help="do everything, accept defaults, no menu")
    parser.add_argument("--dry-run", action="store_true", help="show what would change; change nothing")
    parser.add_argument("--only", type=_names, metavar="STEPS", help="run exactly these steps (comma separated)")
    parser.add_argument("--skip", type=_names, metavar="STEPS", default=[], help="skip these steps")
    parser.add_argument("--list-steps", action="store_true", help="list the steps and exit")
    return parser


def select_steps(args, parser) -> list[str]:
    """Which steps to run: --only, --yes (everything), or the menu; --skip removes names (prerequisites kept)."""
    if args.only:
        names = list(args.only)
    elif args.yes or args.skip:
        names = steps_for_tasks(list(TASKS))
    elif not sys.stdin.isatty():
        parser.error("no terminal for the menu: use --yes, --only or --skip")
    else:
        names = steps_for_tasks(choose_tasks())
    return [name for name in dict.fromkeys(names) if name not in args.skip or name in PREREQUISITE_STEPS]


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_steps:
        return list_steps()
    if args.doctor:
        return doctor.main(["--fix"] if args.fix else [])
    try:
        interactive = sys.stdin.isatty() and not args.yes and not args.only and not args.skip
        names = select_steps(args, parser)
        return run_steps(names, dry_run=args.dry_run, assume_yes=args.yes, interactive=interactive)
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupted. Run ./setup.py again to continue.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
