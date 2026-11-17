#!/usr/bin/env python3
"""Set up (or check) the AI Software Factory: MLflow, Beads/Dolt and Scion.

    ./setup.py             menu: choose which setup steps to run
    ./setup.py --doctor    only check the installation and print fixes
    ./setup.py --help      all options
    ./setup.py --venv-only create/update the Python environment only

Needs only Python 3.10+. Creates .venv with the packages from
scripts/requirements.txt, then runs scripts/setup.py inside it.
(This is not a setuptools file; the repository is not a Python package.)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = Path(os.environ.get("ASF_PYTHON_VENV", str(ROOT / ".venv")))
PYTHON = VENV / "bin" / "python"
REQUIREMENTS = ROOT / "scripts" / "requirements.txt"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"


def fail(*lines):
    for line in lines:
        print(line, file=sys.stderr)
    sys.exit(1)


def has_pip(python_bin=PYTHON):
    if not python_bin.exists():
        return False
    return subprocess.run(
        [str(python_bin), "-m", "pip", "--version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def bootstrap_pip(python_bin=PYTHON):
    """Ensure `pip` is installed inside `VENV`, falling back when `ensurepip` is absent."""
    if has_pip(python_bin):
        return True
    # 1. Try ensurepip inside the venv
    subprocess.run(
        [str(python_bin), "-m", "ensurepip", "--upgrade"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if has_pip(python_bin):
        return True
    # 2. If `uv` is installed on the host, `uv pip install pip` works without ensurepip
    uv = shutil.which("uv")
    if uv:
        subprocess.run(
            [uv, "pip", "install", "--python", str(python_bin), "--quiet", "pip"],
            check=False,
        )
        if has_pip(python_bin):
            return True
    # 3. Download official get-pip.py and run it with the venv's python
    print("Bootstrapping pip into .venv via get-pip.py ...")
    with tempfile.NamedTemporaryFile(suffix="-get-pip.py", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with urllib.request.urlopen(GET_PIP_URL, timeout=60) as resp:
            tmp_path.write_bytes(resp.read())
        subprocess.run([str(python_bin), str(tmp_path), "--quiet", "--disable-pip-version-check"], check=False)
    except Exception as error:
        print(f"Warning: get-pip.py bootstrap failed: {error}", file=sys.stderr)
    finally:
        tmp_path.unlink(missing_ok=True)
    return has_pip(python_bin)


def ensure_venv(venv_dir=VENV, python_bin=PYTHON, requirements=REQUIREMENTS):
    """Create `.venv` (even on systems without `ensurepip` or system `pip`) and install requirements."""
    if not python_bin.exists():
        print("Creating the Python environment (.venv) ...")
        try:
            venv.create(venv_dir, with_pip=True)
        except Exception:
            # Debian/Ubuntu without python3-venv/ensurepip fails with with_pip=True;
            # create the venv without pip and bootstrap pip into it below.
            shutil.rmtree(venv_dir, ignore_errors=True)
            try:
                venv.create(venv_dir, with_pip=False)
            except Exception as error:
                shutil.rmtree(venv_dir, ignore_errors=True)
                fail(
                    f"Could not create .venv: {error}",
                    "Ubuntu/Debian: sudo apt install python3-venv python3-pip   then run ./setup.py again.",
                )

    if not bootstrap_pip(python_bin):
        uv = shutil.which("uv")
        if uv:
            install = subprocess.run([uv, "pip", "install", "--python", str(python_bin), "--quiet", "-r", str(requirements)])
            if install.returncode == 0:
                return
        fail(
            "Could not bootstrap pip inside .venv.",
            "Ubuntu/Debian: sudo apt install python3-venv python3-pip   then run ./setup.py again.",
        )

    install = subprocess.run(
        [str(python_bin), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r", str(requirements)]
    )
    if install.returncode != 0:
        fail("Installing Python packages failed (see above). Check internet/proxy and run ./setup.py again.")


def main():
    if sys.version_info < (3, 10):
        fail("Python 3.10 or newer is required.")
    ensure_venv()
    if sys.argv[1:] == ["--venv-only"]:
        return
    script = ROOT / "scripts" / "setup.py"
    os.execv(str(PYTHON), [str(PYTHON), str(script), *sys.argv[1:]])


if __name__ == "__main__":
    main()
