"""Choose where to install Scion, preferring an existing binary, before the build.

Search PATH and common install locations, save the choice in asf.local.yaml,
and offer to remove duplicate binaries. A fresh install needs no existing binary.

    python scripts/steps/scion_cli.py --dry-run
"""
import os
import shlex
import shutil
import sys
from pathlib import Path

try:  # run as a script: put scripts/ on sys.path
    import _path  # noqa: F401
except ModuleNotFoundError:  # imported as steps.scion_cli: scripts/ is already on sys.path
    pass

import factory_env as fe
from steps import ui
from steps.common import Context, StepError, StepResult, cli


def common_paths() -> list[Path]:
    return [Path.home() / ".local/bin/scion", Path.home() / ".config/scion/scion",
            Path("/usr/local/bin/scion")]


def discover() -> list[Path]:
    """PATH order first (like `which -a scion`), then other known locations."""
    first = shutil.which("scion")
    candidates = ([Path(first).absolute()] if first else [])
    candidates += [Path(directory or ".").absolute() / "scion"
                   for directory in os.get_exec_path()]
    candidates += [fe.install_path(), *common_paths()]
    return list(dict.fromkeys(path for path in candidates
                              if path.is_file() and os.access(path, os.X_OK)))


def _remove_duplicates(ctx: Context, install: Path, binaries: list[Path]) -> None:
    # Keep aliases to the chosen executable: deleting their target would break it.
    duplicates = [path for path in binaries if path.resolve() != install.resolve()]
    if not duplicates:
        return
    ui.info("Other Scion binaries found:")
    for path in duplicates:
        ui.info(f"  {path}")
    if ctx.assume_yes or not ui.confirm("Remove these duplicate Scion binaries?", default=False):
        return
    for path in duplicates:
        try:
            path.unlink()
        except OSError as error:
            raise StepError(f"Could not remove {path}: {error}",
                            [f"remove it manually if desired: sudo rm -- {shlex.quote(str(path))}",
                             "./setup.py --only scion_cli,build_scion"]) from None
        ui.info(f"removed {path}")


def run(ctx: Context, **options) -> StepResult:
    ui.heading("Scion binary location")
    binaries = discover()
    candidates = list(dict.fromkeys([*binaries, fe.install_path(), *common_paths()]))
    recommended = candidates[0]
    for path in binaries:
        ui.info(f"Found Scion: {path}")
    if not binaries:
        ui.info("No Scion binary found. The build will create and install it.")
    ui.info(f"Recommended install location: {recommended}")
    if ctx.dry_run:
        ctx.log("would ask where to install Scion and whether to remove duplicate binaries")
        return StepResult("dry-run", f"Scion install location: recommend {recommended}")
    choices = {str(path): "recommended" if path == recommended else "existing binary" if path in binaries
               else "install here" for path in candidates}
    install = recommended if ctx.assume_yes else Path(ui.choose("Scion install location", choices))
    if install != fe.install_path():
        fe.set_local("scion.install_path", str(install))
        ui.info(f"saved scion.install_path: {install}")
    _remove_duplicates(ctx, install, binaries)
    on_path = shutil.which("scion") == str(install)
    if not on_path:
        os.environ["PATH"] = f"{install.parent}{os.pathsep}{os.environ.get('PATH', '')}"
        ui.info("To use Scion in your terminal after the build, run:")
        print(f'    export PATH={shlex.quote(str(install.parent))}:"$PATH"')
        ui.info("Add that line to your shell profile to keep it for future terminals.")
    return StepResult("done", f"Scion will be installed at {install}")


main = cli(run, None, __doc__, needs_adapter=False)

if __name__ == "__main__":
    sys.exit(main())
