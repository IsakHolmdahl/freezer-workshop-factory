#!/usr/bin/env python3
"""Verify the local Scion install: is it running, and does the broker match the CLI version?

Checks:
  1. `scion` is on PATH and reports a version.
  2. The Runtime Broker answers /healthz and reports the same version as the CLI.
  3. The Hub answers /healthz (warning only; use --require-hub to make it fatal).
  4. The installed binary matches scion/build/scion (warning only).

Exit code 0 when all required checks pass, 1 otherwise.
"""
import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests
from scion_install import upstream_binary

BUILT_BINARY = Path(__file__).resolve().parents[1] / "scion" / "build" / "scion"


def cli_version(binary):
    """Return (version, commit) parsed from `scion version` ('scion <ver> (commit <sha>)')."""
    out = subprocess.run([binary, "version"], capture_output=True, text=True, timeout=30).stdout
    match = re.search(r"^scion (\S+) \(commit (\w+)\)", out, re.MULTILINE)
    return (match.group(1), match.group(2)) if match else (None, None)


def healthz(url):
    response = requests.get(f"{url.rstrip('/')}/healthz", timeout=5)
    response.raise_for_status()
    return response.json()


def versions_match(broker_version, version, commit):
    if not broker_version:
        return False
    return broker_version == version or (commit and (commit.startswith(broker_version) or broker_version.startswith(commit)))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--broker-url", default="http://127.0.0.1:9800")
    parser.add_argument("--hub-url", default="http://127.0.0.1:8080")
    parser.add_argument("--require-hub", action="store_true", help="fail if the Hub is unreachable")
    args = parser.parse_args(argv)

    failures, warnings = [], []

    def report(ok, message, fatal=True):
        print(f"[{'ok' if ok else ('FAIL' if fatal else 'warn')}] {message}")
        if not ok:
            (failures if fatal else warnings).append(message)

    binary = shutil.which("scion")
    report(binary is not None, f"scion on PATH: {binary or 'not found'}")
    if not binary:
        return 1
    version, commit = cli_version(binary)
    report(version is not None, f"CLI version: {version} (commit {commit})")

    try:
        broker = healthz(args.broker_url)
        report(True, f"broker running at {args.broker_url} (status: {broker.get('status')})")
        report(broker.get("status") in ("healthy", "ok"), f"broker status is {broker.get('status')}",
               fatal=False)
        report(versions_match(broker.get("version"), version, commit),
               f"broker version {broker.get('version')} matches CLI {version}/{commit}")
    except (requests.RequestException, ValueError) as error:
        report(False, f"broker not reachable at {args.broker_url}: {error}")

    try:
        hub = healthz(args.hub_url)
        report(True, f"hub running at {args.hub_url} (status: {hub.get('status')})")
        if hub.get("version"):
            report(versions_match(hub["version"], version, commit),
                   f"hub version {hub['version']} matches CLI {version}/{commit}", fatal=False)
    except (requests.RequestException, ValueError) as error:
        report(False, f"hub not reachable at {args.hub_url}: {error}", fatal=args.require_hub)

    if BUILT_BINARY.is_file():
        report(sha256(upstream_binary(binary)) == sha256(BUILT_BINARY),
               f"installed binary matches {BUILT_BINARY}", fatal=False)

    print(f"{len(failures)} failure(s), {len(warnings)} warning(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
