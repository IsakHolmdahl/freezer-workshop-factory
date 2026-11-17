#!/usr/bin/env python3
"""Build (and optionally push) all ASF container images in dependency order.

Used by .github/workflows/build.yml; runs locally too.

    ci-images.py list
    ci-images.py build --registry ghcr.io/dewire --tag sha-abc123 [--tag latest]
                       [--push --platforms linux/amd64,linux/arm64] [--only asf-opencode,...]

Without --push the images are built with plain `docker build` into the local
Docker (one platform), so later images can use earlier ones as their base.
With --push each image is built with `docker buildx build --push` for the given
platforms; bases are referenced by the first tag, which is pushed first.
"""
import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCION = "scion"  # submodule
TOOL_LAYERS = "harness-configs/asf-opencode/tools"
# Same variants as scripts/sync-factory.py DEFAULT_VARIANTS (tool layers in this order).
VARIANTS = ("pnpm", "python", "pnpm,python")


@dataclass(frozen=True)
class Image:
    name: str
    context: str
    dockerfile: str
    base: str | None = None          # name of another Image used as BASE_IMAGE
    args: tuple = ()                  # extra KEY=VALUE build args
    platforms: str | None = None      # override (e.g. amd64-only images)


def images(commit):
    git = (f"GIT_COMMIT={commit}",)
    plan = [
        Image("mlflow-full", "mlflow", "mlflow/Dockerfile"),
        # dolt/Dockerfile.dolt downloads the linux/amd64 Dolt release only.
        Image("ai-software-factory-dolt", "dolt", "dolt/Dockerfile.dolt", platforms="linux/amd64"),
        Image("asf-run-exporter", "telemetry/run-exporter", "telemetry/run-exporter/Dockerfile"),
        Image("scion-core-base", f"{SCION}/image-build", f"{SCION}/image-build/core-base/Dockerfile", args=git),
        Image("scion-base", SCION, f"{SCION}/image-build/scion-base/Dockerfile", base="scion-core-base", args=git),
        Image("asf-opencode", "harness-configs/asf-opencode", "harness-configs/asf-opencode/Dockerfile", base="scion-base"),
    ]
    for variant in VARIANTS:
        tools = variant.split(",")
        parent = "-".join(["asf-opencode", *tools[:-1]])
        plan.append(Image("-".join(["asf-opencode", *tools]), f"{TOOL_LAYERS}/{tools[-1]}",
                          f"{TOOL_LAYERS}/{tools[-1]}/Dockerfile", base=parent))
    return plan


def with_bases(plan, only):
    """The requested images plus everything they build on, in plan order."""
    by_name = {image.name: image for image in plan}
    unknown = set(only) - set(by_name)
    if unknown:
        raise SystemExit(f"unknown image(s): {', '.join(sorted(unknown))}")
    wanted = set()
    for name in only:
        while name and name not in wanted:
            wanted.add(name)
            name = by_name[name].base
    return [image for image in plan if image.name in wanted]


def commands(image, registry, tags, push, platforms):
    refs = [f"{registry}/{image.name}:{tag}" for tag in tags]
    build_args = list(image.args)
    if image.base:
        build_args.append(f"BASE_IMAGE={registry}/{image.base}:{tags[0]}")
    argv = (["docker", "buildx", "build", "--platform", image.platforms or platforms, "--push",
             "--cache-from", f"type=registry,ref={registry}/{image.name}:buildcache",
             "--cache-to", f"type=registry,ref={registry}/{image.name}:buildcache,mode=max"]
            if push else ["docker", "build"])
    for arg in build_args:
        argv += ["--build-arg", arg]
    for ref in refs:
        argv += ["-t", ref]
    return argv + ["-f", image.dockerfile, image.context]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    build = sub.add_parser("build")
    build.add_argument("--registry", required=True, help="e.g. ghcr.io/dewire (lowercase)")
    build.add_argument("--tag", action="append", required=True, help="repeat; the first is used for bases")
    build.add_argument("--push", action="store_true")
    build.add_argument("--platforms", default="linux/amd64")
    build.add_argument("--only", default="", help="comma-separated image names (plus their bases)")
    build.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "build":
        import scion_source

        scion_source.ensure_source(dry_run=args.dry_run)
    commit = subprocess.run(["git", "-C", str(ROOT / SCION), "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip() or "unknown"
    plan = images(commit)
    if args.command == "list":
        for image in plan:
            print(f"{image.name:<28} base={image.base or '-'}")
        return 0
    if args.only:
        plan = with_bases(plan, [n.strip() for n in args.only.split(",") if n.strip()])
    env = {**os.environ, "DOCKER_BUILDKIT": "1"}
    for image in plan:
        argv = commands(image, args.registry.rstrip("/").lower(), args.tag, args.push, args.platforms)
        print(f"::group::{image.name}\n+ {' '.join(argv)}", flush=True)
        if not args.dry_run:
            subprocess.run(argv, cwd=ROOT, env=env, check=True)
        print("::endgroup::", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
