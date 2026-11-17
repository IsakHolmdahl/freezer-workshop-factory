#!/usr/bin/env python3
"""Publish the pinned process skill only when missing; reject content drift."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
NAME = "software-engineering-process"


def hashes(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def verify_files(expected, actual, source):
    if actual != expected:
        raise ValueError(f"{source} differs from the process-skill lock; publish a new version and update the lock and template pins")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-local", action="store_true", help="verify local files and template references without accessing the Hub")
    parser.add_argument("--agent-home", type=Path, help="verify the resolved version and installed Pi skill in an agent home")
    args = parser.parse_args()
    lock = json.loads((ROOT / "skills" / f"{NAME}.lock.json").read_text())
    skill_dir = ROOT / "skills" / NAME
    verify_files(lock["files"], hashes(skill_dir), "Local skill")
    uri = f"skill://scion/global/{NAME}"
    for role in ("coordinator", "code-reviewer", "investigator"):
        config = (ROOT / "templates" / role / "scion-agent.yaml").read_text()
        if f'uri: "{uri}"' not in config:
            raise ValueError(f"{role} must reference {uri}")
    if args.agent_home:
        manifest = json.loads((args.agent_home / ".scion/resolved-skills.json").read_text())
        resolved = next(item for item in manifest["skills"] if item.get("name") == NAME)
        if resolved.get("uri") != lock["uri"] or resolved.get("resolvedVersion") != lock["version"]:
            raise ValueError("Agent has a stale process-skill resolution; provision a fresh agent")
        verify_files(lock["files"], hashes(args.agent_home / ".config/opencode/skills" / NAME), "Installed skill")
        print("Process skill: agent resolution and installed bytes verified")
        return
    if args.check_local:
        print("Process skill: local hashes and template references verified")
        return

    def scion_json(*arguments):
        result = subprocess.run(["scion", "--global", "--non-interactive", "--format", "json", *arguments],
                                check=True, capture_output=True, text=True)
        return json.loads(result.stdout)

    # Query the global scope explicitly; name-only lookup can select a project skill.
    catalog = scion_json("skills", "list")
    # Resolve is authoritative for scope/version; listing only avoids treating a
    # network or auth failure as permission to publish.
    if catalog is None:
        entries = []
    elif isinstance(catalog, list):
        entries = catalog
    else:
        entries = catalog.get("skills", [])
    skill = next((item for item in entries if item.get("name") == NAME and item.get("scope") == "global"), None)
    versions = scion_json("skills", "versions", skill["id"]) if skill else []
    if not any(item.get("version") == lock["version"] for item in versions):
        command = ["scion", "--global", "--non-interactive", "skills", "publish", str(skill_dir),
                   "--version", lock["version"], "--scope", "global"]
        if skill:
            command.extend(["--skill-id", skill["id"]])
        subprocess.run(command, check=True)
    resolved = scion_json("skills", "resolve", lock["uri"])
    if resolved.get("resolvedVersion") != lock["version"]:
        raise ValueError("Hub resolved an unexpected process-skill version")
    remote = {item["path"]: item["hash"].removeprefix("sha256:") for item in resolved["files"]}
    verify_files(lock["files"], remote, "Published skill")
    print(f"Verified {lock['uri']} ({resolved['contentHash']})")


if __name__ == "__main__":
    main()
