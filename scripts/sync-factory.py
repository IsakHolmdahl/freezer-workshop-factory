#!/usr/bin/env python3
"""One-command onboarding and sync for the AI Software Factory stack.

1. Builds the Scion base image, builds the scion binary (`make all`) and installs it to
   $INSTALL_PATH (default ~/.local/bin/scion), then builds and installs `asf-opencode` and its tool
   variants (`pnpm`, `python`) with that binary.
2. Computes a content-addressed semver (`1.0.<hash>`) over `skills/` so changed skills
   are always published as a new version in the Hub while unchanged skills are skipped.
3. Imports and syncs the standard role templates to the Hub.
4. Ensures `DOLT_URL` is set in Hub environment variables.
"""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import factory_env as fe  # noqa: E402
import scion_source  # noqa: E402
from scion_install import install_binary, install_scion  # noqa: E402,F401
from deployments import get_adapter  # noqa: E402

SCION_DIR = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = SCION_DIR / "scripts" / "build-harness.py"
BASE_IMAGE_SCRIPT = SCION_DIR / "scripts" / "build-scion-base-image.py"
SCION_SRC_DIR = SCION_DIR / "scion"
BASE_IMAGE = "scion-local-base:latest"
DEFAULT_INSTALL_PATH = Path.home() / ".local" / "bin" / "scion"
PUBLISH_SCRIPT = SCION_DIR / "scripts" / "publish-skills-to-hub.sh"
SKILLS_DIR = SCION_DIR / "skills"
TEMPLATES_DIR = SCION_DIR / "templates"
DEFAULT_VARIANTS = ("", "pnpm", "python", "pnpm,python")
DEFAULT_DOLT_URL = "mysql://beads@127.0.0.1:3307"


def skills_semver(skills_dir=SKILLS_DIR):
    """Deterministic `1.0.<int>` semver from all skill files so edits auto-publish."""
    digest = hashlib.sha256()
    root = Path(skills_dir)
    for skill_md in sorted(root.glob("*/SKILL.md")):
        skill_root = skill_md.parent
        for path in sorted(skill_root.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            rel = path.relative_to(root).as_posix()
            digest.update(rel.encode("utf-8") + b"\0")
            digest.update(path.read_bytes() + b"\0")
    patch = int(digest.hexdigest()[:6], 16)
    return f"1.0.{patch}"


def selected_templates(templates_dir=TEMPLATES_DIR):
    return sorted(p.parent for p in Path(templates_dir).glob("*/scion-agent.yaml"))


def run(argv, dry_run=False):
    print("+ " + " ".join(str(a) for a in argv), flush=True)
    if not dry_run:
        subprocess.run([str(a) for a in argv], check=True)


def scion_cli(adapter, args, dry_run=False):
    """Run a Scion CLI command through the deployment adapter (Hub endpoint and execution location)."""
    args = [str(arg) for arg in args]
    print("+ scion " + " ".join(str(a) for a in args), flush=True)
    if dry_run:
        return
    result = adapter.scion(args, timeout=600)
    if not result.ok:
        raise SystemExit(f"scion {args[0]} failed (exit {result.returncode}): {result.stderr.strip()}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--variants", default=";".join(DEFAULT_VARIANTS),
                        help="semicolon-separated tool sets to build/install")
    parser.add_argument("--skills-version", help="explicit semver for skills (default: content hash)")
    parser.add_argument("--dolt-url", default=DEFAULT_DOLT_URL,
                        help="DOLT_URL to register in Hub env (empty to skip)")
    parser.add_argument("--skip-build", action="store_true", help="skip image builds (harness-configs are still installed)")
    parser.add_argument("--skip-harness-install", action="store_true",
                        help="build images only; do not install harness-configs (needs a running Hub)")
    parser.add_argument("--skip-binary", action="store_true", help="skip make all and scion install")
    parser.add_argument("--install-path",
                        default=os.environ.get("INSTALL_PATH", str(DEFAULT_INSTALL_PATH)),
                        help="where to install the scion binary (default: $INSTALL_PATH or ~/.local/bin/scion)")
    parser.add_argument("--skip-skills", action="store_true", help="skip skill publishing")
    parser.add_argument("--skip-templates", action="store_true", help="skip template import/sync")
    parser.add_argument("--tag-registry", default=fe.get("scion.image_registry", ""),
                        help="registry prefix to tag built images with (default: scion.image_registry from asf.yaml)")
    parser.add_argument("--dry-run", action="store_true", help="print commands without executing")
    args = parser.parse_args(argv)
    variants = [v.strip() for v in args.variants.split(";")]
    templates = selected_templates(TEMPLATES_DIR) if not args.skip_templates else []
    skills = sorted(SKILLS_DIR.glob("*/SKILL.md")) if not args.skip_skills else []
    harness_count = len(variants) if not args.skip_harness_install else 0
    total = harness_count + len(skills) + len(templates)
    completed = 0
    def progress(label):
        print(f"Publish [{completed}/{total}] {label}", flush=True)
    if total:
        progress(f"{harness_count} harness configs, {len(skills)} skills, {len(templates)} templates")

    if not args.skip_build or not args.skip_binary:
        scion_source.ensure_source(dry_run=args.dry_run)

    if not args.skip_build:
        cmd = [sys.executable, BASE_IMAGE_SCRIPT]
        if args.tag_registry:
            cmd += ["--tag-registry", args.tag_registry]
        run(cmd, dry_run=args.dry_run)

    if not args.skip_binary:
        install_path = Path(args.install_path)
        run(["make", "-C", SCION_SRC_DIR, "all"], dry_run=args.dry_run)
        print(f"+ install {SCION_SRC_DIR / 'build' / 'scion'} {install_path}", flush=True)
        print(f"+ install ASF shim {install_path} (upstream: {install_path}-real)", flush=True)
        if not args.dry_run:
            install_scion(SCION_SRC_DIR / "build" / "scion", install_path)
        # Later steps (harness install, skills, templates) use this binary.
        os.environ["PATH"] = f"{install_path.parent}{os.pathsep}{os.environ.get('PATH', '')}"

    # Building needs Docker; installing a harness-config needs a running Hub.
    if not (args.skip_build and args.skip_harness_install):
        for tools in variants:
            if not args.skip_harness_install:
                progress(f"Publishing harness: asf-opencode{('-' + tools.replace(',', '-')) if tools else ''}")
            cmd = [sys.executable, BUILD_SCRIPT, "--tools", tools]
            if args.skip_build:
                cmd += ["--no-build"]
            else:
                cmd += ["--base-image", BASE_IMAGE]
                if args.tag_registry:
                    cmd += ["--tag-registry", args.tag_registry]
            if args.skip_harness_install:
                cmd.append("--no-install")
            if args.dry_run:
                cmd.append("--dry-run")
            run(cmd, dry_run=args.dry_run)
            if not args.skip_harness_install:
                completed += 1
                progress("Harness config complete")

    if not args.skip_harness_install:
        # A running Hub uses its own settings, not the host CLI's YAML default.
        # Publish only this scalar so Hub runtime/profile edits remain intact.
        run([sys.executable, SCION_DIR / "scripts/sync-hub-settings.py",
             "--hub", fe.hub_url(), "--default-harness-config",
             fe.get("scion.default_harness_config", "asf-opencode")],
            dry_run=args.dry_run)

    if not args.skip_skills:
        version = args.skills_version or skills_semver(SKILLS_DIR)
        cmd = [PUBLISH_SCRIPT, "--version", version, "--no-rewrite"]
        if args.dry_run:
            cmd.append("--dry-run")
        # The shell publisher reports individual skill completions, including
        # already-published versions, in the same overall counter.
        old = {key: os.environ.get(key) for key in ("ASF_PUBLISH_TOTAL", "ASF_PUBLISH_OFFSET")}
        try:
            os.environ["ASF_PUBLISH_TOTAL"] = str(total)
            os.environ["ASF_PUBLISH_OFFSET"] = str(completed)
            run(cmd, dry_run=args.dry_run)
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        completed += len(skills)

    if not args.skip_templates or args.dolt_url:
        adapter = get_adapter(dry_run=args.dry_run)
    if not args.skip_templates:
        for tpl in templates:
            progress(f"Publishing template: {tpl.name}")
            scion_cli(adapter, ["--global", "--yes", "--non-interactive",
                                "templates", "import", "--force", tpl], args.dry_run)
            scion_cli(adapter, ["--global", "--yes", "--non-interactive",
                                 "templates", "sync", tpl.name], args.dry_run)
            completed += 1
            progress(f"Template complete: {tpl.name}")

    if args.dolt_url:
        scion_cli(adapter, ["--global", "hub", "env", "set", "--scope", "hub", "--as-needed",
                            "DOLT_URL", args.dolt_url], args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
