#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["PyYAML>=6,<7", "requests>=2.32,<3"]
# ///
"""Sync YAML runtime settings to the Hub's database-backed configuration."""

import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

import requests
import yaml

SECTIONS = ("profiles", "runtimes", "harness_configs", "image_registry", "default_harness_config")


def load_settings(path, sections):
    settings = yaml.safe_load(path.read_text())
    if not isinstance(settings, dict):
        raise ValueError("Settings must be a YAML mapping")
    selected = {key: settings[key] for key in sections if key in settings}
    if not selected:
        raise ValueError("No selected runtime settings exist in the YAML file")
    for key, value in selected.items():
        if key in ("image_registry", "default_harness_config"):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} must be a nonempty string")
        elif not isinstance(value, dict):
            raise ValueError(f"{key} must be a mapping (use {{}} to clear it)")
    # Reject values such as YAML timestamps before making any API calls.
    json.dumps(selected, allow_nan=False)
    return selected


def build_update(local, remote):
    """Send complete selected sections, and omit sections already identical."""
    return {key: value for key, value in local.items()
            if value != remote.get(key, {} if isinstance(value, dict) else None)}


def describe_update(update, remote):
    for key, value in update.items():
        if isinstance(value, dict):
            previous = remote.get(key) or {}
            removed = sorted(set(previous) - set(value))
            print(f"  {key}: replace section with {len(value)} named entries")
            if removed:
                print(f"    Remove Hub-only entries: {', '.join(removed)}")
        else:
            print(f"  {key}: update")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, default=Path.home() / ".scion/settings.yaml")
    parser.add_argument("--hub", default="http://localhost:8080", help="Hub base URL")
    parser.add_argument("--sections", nargs="+", choices=SECTIONS, default=list(SECTIONS),
                        help="sync only these sections (default: supported sections present in YAML)")
    parser.add_argument("--dry-run", action="store_true", help="compare without saving")
    parser.add_argument("--default-harness-config", help="publish only this default, without reading YAML")
    args = parser.parse_args(argv)

    try:
        if args.default_harness_config is not None:
            if not args.default_harness_config.strip():
                raise ValueError("default_harness_config must be a nonempty string")
            local = {"default_harness_config": args.default_harness_config}
        else:
            local = load_settings(args.settings.expanduser(), args.sections)
        hub = urlsplit(args.hub)
        if hub.scheme not in ("http", "https") or not hub.netloc or hub.query or hub.fragment or hub.username:
            raise ValueError("--hub must be an HTTP(S) base URL without credentials, query, or fragment")
        endpoint = args.hub.rstrip("/") + "/api/v1/admin/server-config"
        with requests.Session() as session:
            token = os.environ.get("SCION_HUB_TOKEN") or os.environ.get("SCION_DEV_TOKEN")
            if not token:
                state = Path(os.environ.get("SCION_HOME") or Path.home() / ".scion")
                token_path = Path(os.environ.get("SCION_DEV_TOKEN_FILE") or state / "dev-token")
                token = token_path.read_text().strip() if token_path.is_file() else None
            if token:
                session.headers["Authorization"] = f"Bearer {token}"
            response = session.get(endpoint, timeout=30, allow_redirects=False)
            if response.status_code != 200:
                raise ValueError(f"Hub settings read failed (HTTP {response.status_code})")
            remote = response.json()
            if not isinstance(remote, dict):
                raise ValueError("Hub returned an invalid settings response")
            update = build_update(local, remote)
            if not update:
                print("Selected settings already match the Hub.")
                return 0
            print(f"{'Would sync' if args.dry_run else 'Syncing'} {args.settings} to {args.hub}:")
            describe_update(update, remote)
            if args.dry_run:
                print("Dry run: no settings changed. Configuration values are not printed.")
                return 0
            response = session.put(endpoint, json=update, timeout=30, allow_redirects=False)
            if response.status_code != 200:
                raise ValueError(f"Hub settings save failed (HTTP {response.status_code}); check Hub validation logs")
            result = response.json()
            if result.get("status") != "saved":
                raise ValueError("Hub did not confirm that settings were saved")
            print("Saved. The co-located broker receives the update automatically.")
            print("Stop and resume existing agents through Scion to apply mount changes.")
    except (OSError, ValueError, TypeError, yaml.YAMLError, requests.RequestException) as exc:
        # YAML/parser and HTTP exceptions can include configuration values or tokens.
        if isinstance(exc, ValueError) and not isinstance(exc, (json.JSONDecodeError, requests.RequestException)):
            print(f"Error: {exc}", file=sys.stderr)
        else:
            print(f"Error: {type(exc).__name__}; check the settings file and Hub connection.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
