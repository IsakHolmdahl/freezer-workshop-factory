"""Render Compose includes to check repository-relative bind and build paths."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not installed")
def test_included_compose_paths_resolve_inside_checkout():
    env = dict(os.environ)
    env.setdefault("SCION_IMAGE_REGISTRY", "example.invalid/asf")
    output = subprocess.check_output(
        ["docker", "compose", "-f", str(ROOT / "compose.yaml"),
         "config", "--format", "json"], cwd=ROOT, env=env,
    )
    services = json.loads(output)["services"]
    expected = {
        "otel-collector": {"/etc/otelcol/config.yaml": "telemetry/otel-collector.yaml"},
        "prometheus": {"/etc/prometheus/prometheus.yml": "telemetry/prometheus.yml"},
        "grafana": {
            "/etc/grafana/provisioning": "telemetry/grafana/provisioning",
            "/var/lib/grafana/dashboards": "telemetry/grafana/dashboards",
        },
        "mlflow": {"/opt/mlflow-bootstrap": "mlflow/bootstrap"},
        "run-exporter": {"/templates": "templates"},
    }
    for service, targets in expected.items():
        mounts = {v["target"]: v["source"] for v in services[service]["volumes"]}
        for target, relative in targets.items():
            assert mounts[target] == str(ROOT / relative)
    assert "scion" not in services  # Scion runs on the host: `scion server start`
