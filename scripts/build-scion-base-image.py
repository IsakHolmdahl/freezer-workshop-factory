#!/usr/bin/env python3
"""Build the Scion base image (core-base -> scion-base) from the local scion/ folder.

The result is the BASE_IMAGE for the asf-opencode image:

    build-scion-base-image.py
    build-harness.py --base-image scion-local-base:latest
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import factory_env as fe  # noqa: E402
import scion_source  # noqa: E402

DEFAULT_SCION_DIR = Path(__file__).resolve().parents[1] / "scion"


def run(argv):
    print("+ " + " ".join(argv), flush=True)
    subprocess.run(argv, check=True, env={**os.environ, "DOCKER_BUILDKIT": "1"})


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tag", default="latest", help="image tag (default: latest)")
    parser.add_argument("--scion-dir", type=Path,
                        default=Path(os.environ.get("SCION_DIR", DEFAULT_SCION_DIR)))
    parser.add_argument("--tag-registry", default=fe.get("scion.image_registry", ""),
                        help="registry prefix for built images (default: scion.image_registry from asf.yaml)")
    parser.add_argument("--no-pull", action="store_true", help="do not pull newer upstream images")
    args = parser.parse_args()

    scion_source.ensure_source(source=args.scion_dir)
    build = args.scion_dir / "image-build"
    if not (build / "scion-base" / "Dockerfile").is_file():
        print(f"error: missing scion sources in {args.scion_dir}", file=sys.stderr)
        return 1

    git = subprocess.run(["git", "-C", str(args.scion_dir), "rev-parse", "HEAD"],
                         capture_output=True, text=True)
    commit = git.stdout.strip() if git.returncode == 0 else "unknown"
    core_image = f"scion-local-core-base:{args.tag}"
    base_image = f"scion-local-base:{args.tag}"
    prefix = args.tag_registry.rstrip("/")
    core_tags = ["-t", f"{prefix}/scion-core-base:{args.tag}", "-t", core_image] if prefix else ["-t", core_image]
    base_tags = ["-t", f"{prefix}/scion-base:{args.tag}", "-t", base_image] if prefix else ["-t", base_image]

    run(["docker", "build", *([] if args.no_pull else ["--pull"]),
         "--build-arg", f"GIT_COMMIT={commit}", *core_tags,
         "-f", str(build / "core-base" / "Dockerfile"), str(build)])
    run(["docker", "build", "--build-arg", f"BASE_IMAGE={core_image}",
         "--build-arg", f"GIT_COMMIT={commit}", *base_tags,
         "-f", str(build / "scion-base" / "Dockerfile"), str(args.scion_dir)])
    print(f"Done. Use: build-harness.py --base-image {base_image}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
