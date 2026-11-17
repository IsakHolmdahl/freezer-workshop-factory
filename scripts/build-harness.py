#!/usr/bin/env python3
"""Build an `asf-opencode` harness variant with selected tools and install it.

Each optional tool is a directory under harness-configs/asf-opencode/tools/<name>/
with a Dockerfile that starts FROM ${BASE_IMAGE} and declares the commands it adds
on a "# provides: cmd ..." line. To offer a new tool, add such a directory.

The variant is named after its tools in a fixed order, so the same selection
always gives the same name:

    (none)          -> harness asf-opencode,             image asf-opencode:<tag>
    pnpm            -> harness asf-opencode-pnpm,        image asf-opencode-pnpm:<tag>
    pnpm + python   -> harness asf-opencode-pnpm-python, image asf-opencode-pnpm-python:<tag>

Examples:
    build-harness.py                           # choose tools interactively
    build-harness.py --tools pnpm,python       # -> asf-opencode-pnpm-python
    build-harness.py --tools pnpm --no-install --dry-run
Templates or dispatchers select the result with `--harness-config <harness name>`.
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import factory_env as fe  # noqa: E402

SCION_DIR = Path(__file__).resolve().parents[1]
HARNESS_DIR = SCION_DIR / "harness-configs" / "asf-opencode"
TOOLS_DIR = HARNESS_DIR / "tools"
BASE_NAME = "asf-opencode"
BASE_IMAGE_NAME = "asf-opencode"
# Image build inputs and tests are not part of the installed harness-config.
BUNDLE_EXCLUDE = ("tools", "image", "Dockerfile", "asf-entrypoint.py", "node_modules",
                  "__pycache__", "*test.py", "*_test.py", "test_*.py", "*.test.mjs")


class Failure(Exception):
    pass


def available_tools():
    """Tool name -> commands it provides, in the order used for naming."""
    tools = {}
    for dockerfile in sorted(TOOLS_DIR.glob("*/Dockerfile")):
        match = re.search(r"^# provides:(.*)$", dockerfile.read_text(), re.MULTILINE)
        if not match or not match.group(1).split():
            raise Failure(f"{dockerfile} needs a '# provides: <command> ...' line")
        if not (dockerfile.parent / "TOOLS.md").is_file():
            raise Failure(f"{dockerfile.parent} needs a TOOLS.md (agent-facing notes, see tools/README.md)")
        tools[dockerfile.parent.name] = match.group(1).split()
    return tools


def base_config():
    text = (HARNESS_DIR / "config.yaml").read_text()
    image = re.search(r"^image:\s*(\S+)\s*$", text, re.MULTILINE)
    if not image or ":" not in image.group(1):
        raise Failure("harness-configs/asf-opencode/config.yaml needs 'image: <name>:<tag>'")
    return text, image.group(1)


def choose_interactively(tools):
    if not sys.stdin.isatty():
        raise Failure("no --tools given and stdin is not a terminal")
    names = list(tools)
    print("Optional tools (the base always has Node, OpenCode, bd, dolt, and rtk):")
    for number, name in enumerate(names, 1):
        print(f"  {number}. {name:<8} provides {' '.join(tools[name])}")
    answer = input("Select by number or name, comma-separated (empty for none): ").strip()
    chosen = []
    for item in filter(None, (part.strip() for part in answer.split(","))):
        if item.isdigit() and 1 <= int(item) <= len(names):
            chosen.append(names[int(item) - 1])
        elif item in tools:
            chosen.append(item)
        else:
            raise Failure(f"unknown selection: {item}")
    return chosen


def select(requested, tools):
    unknown = sorted(set(requested) - set(tools))
    if unknown:
        raise Failure(f"unknown tool(s): {', '.join(unknown)}; available: {', '.join(tools)}")
    return [name for name in tools if name in requested]


def variant_config(text, image, commands):
    text = re.sub(r"^image:.*$", f"image: {image}", text, count=1, flags=re.MULTILINE)
    match = re.search(r"^(\s*required_image_tools:\s*\[)(.*)\][ \t]*$", text, re.MULTILINE)
    if not match:
        raise Failure("config.yaml needs a one-line 'required_image_tools: [...]'")
    required = [item.strip() for item in match.group(2).split(",") if item.strip()]
    required += [command for command in commands if command not in required]
    return text[:match.start()] + f"{match.group(1)}{', '.join(required)}]" + text[match.end():]


def run(argv, dry_run):
    print("+ " + " ".join(argv), flush=True)
    if not dry_run:
        subprocess.run(argv, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tools", help="comma-separated tools; omit to choose interactively")
    parser.add_argument("--list", action="store_true", help="list available tools and exit")
    parser.add_argument("--base-image", help="Scion base image for the asf-opencode Dockerfile")
    parser.add_argument("--output", type=Path,
                        help="keep the generated harness-config directory here")
    parser.add_argument("--no-build", action="store_true",
                        help="skip docker build and only generate/install the harness config")
    parser.add_argument("--no-install", action="store_true",
                        help="build the image only; do not install the harness config")
    parser.add_argument("--tag-registry", default=fe.get("scion.image_registry", ""),
                        help="registry prefix for built images and config.yaml (default: scion.image_registry from asf.yaml)")
    parser.add_argument("--dry-run", action="store_true", help="print commands without running them")
    args = parser.parse_args()

    tools = available_tools()
    if args.list:
        for name, commands in tools.items():
            print(f"{name}: {' '.join(commands)}")
        return 0
    requested = args.tools.split(",") if args.tools is not None else choose_interactively(tools)
    chosen = select([name.strip() for name in requested if name.strip()], tools)

    text, base_image = base_config()
    tag = base_image.rsplit(":", 1)[1]
    name = "-".join([BASE_NAME, *chosen])
    short_image = f"{'-'.join([BASE_IMAGE_NAME, *chosen])}:{tag}"
    prefix = args.tag_registry.rstrip("/")
    image = f"{prefix}/{short_image}" if prefix else short_image
    print(f"Harness {name}, image {image}, tools: {', '.join(chosen) or 'none'}")

    if not args.no_build:
        base_target = f"{prefix}/{base_image}" if prefix else base_image
        build = ["docker", "build", "-f", str(HARNESS_DIR / "Dockerfile"), "-t", base_target, "-t", base_image]
        if args.base_image:
            build += ["--build-arg", f"BASE_IMAGE={args.base_image}"]
        run(build + [str(HARNESS_DIR)], args.dry_run)
        previous, layers = base_target, [BASE_IMAGE_NAME]
        for tool in chosen:
            layers.append(tool)
            short_layer = f"{'-'.join(layers)}:{tag}"
            layer_image = f"{prefix}/{short_layer}" if prefix else short_layer
            run(["docker", "build", "-f", str(TOOLS_DIR / tool / "Dockerfile"),
                 "--build-arg", f"BASE_IMAGE={previous}", "-t", layer_image, "-t", short_layer,
                 str(TOOLS_DIR / tool)], args.dry_run)
            previous = layer_image

    commands = [command for tool in chosen for command in tools[tool]]
    with tempfile.TemporaryDirectory(prefix=f"{name}-") as temp:
        bundle = args.output or Path(temp) / name
        if bundle.exists() and any(bundle.iterdir()):
            raise Failure(f"--output {bundle} exists and is not empty")
        shutil.copytree(HARNESS_DIR, bundle, ignore=shutil.ignore_patterns(*BUNDLE_EXCLUDE))
        (bundle / "config.yaml").write_text(variant_config(text, image, commands))
        print(f"Harness config written to {bundle}")
        if not args.no_install:
            run(["scion", "--global", "--yes", "--non-interactive", "harness-config", "install",
                 "--name", name, "--force", f"file://{bundle}"], args.dry_run)
    print(f"Done. Use it with: --harness-config {name}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Failure, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
