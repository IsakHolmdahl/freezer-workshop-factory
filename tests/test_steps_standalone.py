"""Every step is a standalone CLI: `python scripts/steps/<x>.py --help` exits 0."""
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
STEPS = ["services", "testcontainers", "scion_settings_step", "scion_start", "hub_wiring", "cross_project", "doctor_step", "copilot"]
# Captured before the autouse guard patches subprocess: `--help` never starts a process.
REAL_RUN = subprocess.run


@pytest.mark.parametrize("step", STEPS)
def test_step_help_exits_zero(step):
    result = REAL_RUN([sys.executable, str(SCRIPTS / "steps" / f"{step}.py"), "--help"],
                      capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "--dry-run" in result.stdout


@pytest.mark.parametrize("step", STEPS)
def test_step_is_importable(step):
    import importlib

    module = importlib.import_module(f"steps.{step}")
    assert callable(module.run) and callable(module.main)
