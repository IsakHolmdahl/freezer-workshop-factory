"""Create and maintain the Scion settings file (~/.scion/settings.yaml).

image_registry and default_harness_config follow asf.yaml scion settings. A first-time user gets a working file without editing YAML: hub endpoint,
identity, default template/harness, image registry, the shared dirs every
project gets (scratchpad, build-cache) and the Testcontainers socket mount.
Values Scion writes itself (broker id, linked project, ...) and the user's own
choices are kept.
"""
import copy
import os
import shutil
import time
from pathlib import Path

import yaml

import factory_env as fe

TESTCONTAINERS_TARGET_SOCKET = "/run/testcontainers/docker.sock"
# Shared dirs every project's agents get (mounted at /scion-volumes/<name>).
# Global shared_dirs replace the Hub's per-project list, so scratchpad (the Hub
# default) must be listed too. build-cache holds Gradle/pnpm/pip caches
# (see harness-configs/asf-opencode/asf-entrypoint.py).
DEFAULT_SHARED_DIRS = ("scratchpad", "build-cache")


def settings_path():
    return fe.scion_home() / "settings.yaml"


def rootless_docker_socket():
    """Host path to the rootless Docker socket for Testcontainers."""
    configured = os.environ.get("TESTCONTAINERS_SOCKET") or fe.get("testcontainers.socket", "")
    if configured:
        return fe.expand(configured)
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime_dir) / "docker.sock"


def testcontainers_volume(sock=None):
    """Volume mount for the rootless Docker socket when enabled and present, else None."""
    if not fe.get("testcontainers.enabled", True):
        return None
    sock = Path(sock or rootless_docker_socket())
    if not sock.is_socket():
        return None
    return {"source": str(sock), "target": TESTCONTAINERS_TARGET_SOCKET}


def defaults(values):
    """Settings a first start needs; `values` are the .env/.env.secrets settings."""
    auth = {}
    if values.get("SCION_SERVER_AUTH_EMAIL") or values.get("SCION_ADMIN_EMAIL"):
        auth["email"] = values.get("SCION_SERVER_AUTH_EMAIL") or values.get("SCION_ADMIN_EMAIL")
    if values.get("SCION_SERVER_AUTH_DISPLAYNAME"):
        auth["display_name"] = values["SCION_SERVER_AUTH_DISPLAYNAME"]
    result = {
        "schema_version": "1",
        "active_profile": "local",
        "default_template": "coordinator",
        "default_harness_config": fe.get("scion.default_harness_config", "asf-opencode"),
        "image_registry": fe.get("scion.image_registry"),
        "cli": {"autohelp": True},
        "hub": {"enabled": True, "endpoint": fe.hub_url().replace("127.0.0.1", "localhost")},
        "runtimes": {"docker": {"type": "docker"}},
        "profiles": {"local": {"runtime": "docker"}},
    }
    if auth:
        result["server"] = {"auth": auth}
    return result


def _fill_missing(target, source):
    """Add keys from source that target lacks (recursively); never overwrite."""
    for key, value in source.items():
        if key not in target or target[key] is None:
            target[key] = copy.deepcopy(value)
        elif isinstance(target[key], dict) and isinstance(value, dict):
            _fill_missing(target[key], value)


def telemetry_block():
    enabled = bool(fe.get("telemetry.enabled", True))
    endpoint = str(fe.get("telemetry.otlp_endpoint", "localhost:4317") or "localhost:4317")
    return {
        "enabled": enabled,
        "cloud": {
            "enabled": enabled,
            "endpoint": endpoint,
            "protocol": "grpc",
            "tls": {"enabled": False},
        },
    }


def render(current, values, tc_socket=None):
    """Return the new settings dict for `current` (possibly empty)."""
    data = copy.deepcopy(current or {})
    _fill_missing(data, defaults(values))
    # ASF owns the default, including upgrades from Scion's embedded defaults.
    data["default_harness_config"] = defaults(values)["default_harness_config"]
    local_profile = data.setdefault("profiles", {}).setdefault("local", {})
    overrides = local_profile.setdefault("harness_overrides", {}) or {}
    local_profile["harness_overrides"] = overrides
    # asf.yaml is the single source of the registry and telemetry: always follow it.
    if fe.get("scion.image_registry"):
        data["image_registry"] = fe.get("scion.image_registry")
    tel = data.get("telemetry") if isinstance(data.get("telemetry"), dict) else {}
    cloud = {k: v for k, v in (tel.get("cloud") or {}).items() if k != "insecure"} if isinstance(tel.get("cloud"), dict) else {}
    wanted_tel = telemetry_block()
    data["telemetry"] = {**tel, **wanted_tel, "cloud": {**cloud, **wanted_tel["cloud"]}}
    shared = data.get("shared_dirs") if isinstance(data.get("shared_dirs"), list) else []
    names = {d.get("name") for d in shared if isinstance(d, dict)}
    wanted = fe.get("scion.shared_dirs") or DEFAULT_SHARED_DIRS
    data["shared_dirs"] = shared + [{"name": n} for n in wanted if n not in names]
    # Rootless Docker socket for Testcontainers: kept in profiles.local.volumes
    # (asf-entrypoint.py exports DOCKER_HOST/TESTCONTAINERS_* when mounted).
    pvols = [v for v in (local_profile.get("volumes") or [])
             if isinstance(v, dict) and v.get("target") != TESTCONTAINERS_TARGET_SOCKET]
    tc = testcontainers_volume(tc_socket)
    if tc:
        pvols.append(tc)
    if pvols:
        local_profile["volumes"] = pvols
    else:
        local_profile.pop("volumes", None)
    if not overrides:
        local_profile.pop("harness_overrides", None)
    return data


def ensure(values, path=None, tc_socket=None):
    """Write the settings file if it changes; returns (path, changed, backup_path)."""
    path = Path(path or settings_path())
    current = yaml.safe_load(path.read_text()) if path.is_file() else {}
    updated = render(current, values, tc_socket)
    if updated == current:
        return path, False, None
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.is_file():
        backup = path.with_name(f"settings.yaml.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
    path.write_text("# Managed by ASF ./setup.py: your own changes are kept.\n"
                    + yaml.safe_dump(updated, sort_keys=False, default_flow_style=False))
    return path, True, backup
