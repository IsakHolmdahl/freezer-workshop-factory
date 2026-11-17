"""Write the Scion settings file (~/.scion/settings.yaml) for this deployment.

The adapter decides the content (render_settings) and location (settings_path);
this step only compares and writes atomically with mode 0600.
Idempotent: an up-to-date file is skipped.

    python scripts/steps/scion_settings_step.py --dry-run
"""
import os
import sys
import tempfile
from pathlib import Path

try:  # run as a script: put scripts/ on sys.path
    import _path  # noqa: F401
except ModuleNotFoundError:  # imported as steps.scion_settings_step
    pass

import yaml
import factory_env as fe

from steps.common import Context, StepError, StepResult, cli

HEADER = "# Managed by ASF setup: your own changes are kept.\n"


def run(ctx: Context, **options) -> StepResult:
    if not ctx.dry_run and getattr(ctx.adapter, "name", "") in ("local", "compose"):
        try:
            fe.ensure_scion_home()
        except ValueError as error:
            raise StepError(str(error), ["migrate existing Scion data or update SCION_HOME in .env"]) from None
    path = Path(ctx.adapter.settings_path())
    current = _load(path)
    wanted = ctx.adapter.render_settings(current)
    if wanted == current and path.is_file():
        return StepResult("skipped", f"{path} is up to date", {"path": str(path)})
    if ctx.dry_run:
        ctx.log(f"would write {path} (mode 0600)")
        return StepResult("dry-run", f"{path}: would update", {"path": str(path)})
    _write_atomic(path, wanted, backup=current if path.is_file() else None, log=ctx.log)
    return StepResult("done", f"updated {path}", {"path": str(path)})


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as error:
        raise StepError(f"{path} is not valid YAML: {error}",
                        [f"move it aside (mv {path} {path}.broken) and re-run this step"]) from None
    return data or {}


def _write_atomic(path: Path, data: dict, backup, log) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup is not None:
        saved = path.with_name(f"{path.name}.bak")
        saved.write_bytes(path.read_bytes())
        log(f"previous version kept at {saved}")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(HEADER + yaml.safe_dump(data, sort_keys=False, default_flow_style=False))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


main = cli(run, None, __doc__)

if __name__ == "__main__":
    sys.exit(main())
