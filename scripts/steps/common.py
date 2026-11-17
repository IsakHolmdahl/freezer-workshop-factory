"""Shared contract for setup steps: Context, StepResult, StepError and the CLI wrapper.

Every step module exposes run(ctx, **options) -> StepResult (idempotent) and a
main() built with cli(), so it runs standalone and is importable.
Exit codes: 0 done/skipped, 1 StepError, 2 usage, 130 interrupted.
"""
import argparse
import sys
from dataclasses import dataclass, field
from typing import Callable, Literal

from deployments.base import DeployError
from steps import ui


class StepError(Exception):
    """User-facing failure: say what happened and how to fix it."""

    def __init__(self, message: str, fix=()):
        super().__init__(message)
        self.message = message
        self.fix = list(fix)


@dataclass
class StepResult:
    status: Literal["done", "skipped", "dry-run"]
    message: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class Context:
    adapter: object
    cfg: dict = field(default_factory=dict)
    dry_run: bool = False
    assume_yes: bool = False
    log: Callable[[str], None] = print

    @classmethod
    def from_args(cls, args, *, needs_adapter: bool = True) -> "Context":
        """Build the context from parsed CLI args; without an adapter `adapter` is None."""
        if not needs_adapter:
            import factory_env as fe

            return cls(adapter=None, cfg=fe.config(), dry_run=args.dry_run, assume_yes=args.yes, log=print)
        from deployments import get_adapter  # imported lazily

        adapter = get_adapter(dry_run=args.dry_run)
        return cls(adapter=adapter, cfg=getattr(adapter, "cfg", {}), dry_run=args.dry_run,
                   assume_yes=args.yes, log=print)


def common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true", help="show what would change; change nothing")
    parser.add_argument("--yes", action="store_true", help="accept defaults, no questions")
    parser.add_argument("--verbose", action="store_true", help="print every command")


def cli(run: Callable, add_arguments: Callable | None, doc: str, *,
        needs_adapter: bool = True) -> Callable[[list[str] | None], int]:
    """Build a main(argv) for a step module: parse, build Context, run, report.

    needs_adapter=False: the step does not use a deployment adapter (ctx.adapter is None).
    """

    def main(argv: list[str] | None = None) -> int:
        parser = argparse.ArgumentParser(description=doc.strip().splitlines()[0] if doc else None,
                                         epilog=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
        common_args(parser)
        if add_arguments:
            add_arguments(parser)
        args = parser.parse_args(argv)
        ui.set_assume_yes(args.yes)
        options = {k: v for k, v in vars(args).items() if k not in ("dry_run", "yes", "verbose")}
        try:
            ctx = Context.from_args(args, needs_adapter=needs_adapter)
            result = run(ctx, **options)
        except StepError as error:
            return _report_error(error.message, error.fix)
        except DeployError as error:
            return _report_error(error.message, error.fix)
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)
            return 130
        mark = {"done": "✔", "skipped": "↷", "dry-run": "…"}[result.status]
        print(f"{mark} {result.message}")
        return 0

    return main


def _report_error(message: str, fix) -> int:
    print(f"✘ {message}", file=sys.stderr)
    for line in fix:
        print(f"  try: {line}", file=sys.stderr)
    return 1
