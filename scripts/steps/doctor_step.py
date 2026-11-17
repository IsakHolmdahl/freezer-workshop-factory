"""Check the installation (the final check of setup); --fix applies the safe automatic repairs.

Common checks come from doctor.py; the adapter adds its own (for example the
Compose version for compose/containers). Exit 1 when any check fails.

    python scripts/steps/doctor_step.py --dry-run
    python scripts/steps/doctor_step.py --fix
"""
import sys

try:  # run as a script: put scripts/ on sys.path
    import _path  # noqa: F401
except ModuleNotFoundError:  # imported as steps.doctor_step
    pass

from steps.common import Context, StepError, StepResult, cli


def run(ctx: Context, **options) -> StepResult:
    import doctor

    if ctx.dry_run and options.get("fix"):
        ctx.log("would apply the automatic repairs of the failed checks")
    sections = list(doctor.all_checks(quick=options.get("quick", False)))
    if options.get("fix") and not ctx.dry_run:
        doctor.apply_fixes(sections)
        sections = list(doctor.all_checks(quick=options.get("quick", False)))
    results = [result for _, group in sections for result in group]
    failed = [r for r in results if r.status == doctor.FAIL]
    if ctx.dry_run:
        return StepResult("dry-run", f"doctor: {len(results)} checks would run")
    doctor.print_results(sections)
    if failed:
        raise StepError(f"{len(failed)} check(s) failed: " + "; ".join(r.title for r in failed[:3]),
                        [fix for r in failed for fix in r.fix][:3] or ["./setup.py --doctor --fix"])
    return StepResult("done", f"{len(results)} checks passed")


def add_arguments(parser):
    parser.add_argument("--fix", action="store_true", help="apply the safe automatic repairs first")
    parser.add_argument("--quick", action="store_true", help="skip the model calls to each endpoint")


main = cli(run, add_arguments, __doc__)

if __name__ == "__main__":
    sys.exit(main())
