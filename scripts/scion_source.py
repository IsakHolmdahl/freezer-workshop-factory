"""Select the Scion Git checkout used by ASF builds (Git resolves tags/refs)."""
import sys

from dotenv import dotenv_values

import factory_env as fe


def source_ref():
    return fe.settings().get("SCION_SOURCE_REF") or dotenv_values(fe.ENV_TEMPLATE).get("SCION_SOURCE_REF")


def ensure_source(*, dry_run=False, source=None):
    source = source or fe.SCION_SRC
    ref = source_ref()
    if not ref or ref.startswith("-"):
        raise RuntimeError("SCION_SOURCE_REF must be a Git tag, branch or commit in .env")
    git = ["git", "-C", str(source)]

    def run(args):
        result = fe.run(args, timeout=600)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or f"command failed: {' '.join(args)}")
        return result.stdout.strip()

    if dry_run:
        print(f"would select Scion {ref} in {source}")
        return False
    if not (source / "Makefile").is_file():
        run(["git", "submodule", "update", "--init", "scion"])
    resolved = fe.run([*git, "rev-parse", "--verify", f"{ref}^{{commit}}"])
    if resolved.returncode:
        run([*git, "fetch", "origin", ref])
        target = run([*git, "rev-parse", "--verify", "FETCH_HEAD^{commit}"])
    else:
        target = resolved.stdout.strip()
    if run([*git, "rev-parse", "HEAD"]) == target:
        return False
    if run([*git, "status", "--porcelain"]):
        raise RuntimeError("Scion source has local changes; save them before changing SCION_SOURCE_REF")
    run([*git, "checkout", "--detach", target])
    print(f"Scion source: {ref} ({target[:12]})")
    return True


if __name__ == "__main__":
    try:
        ensure_source()
    except RuntimeError as error:
        sys.exit(str(error))
