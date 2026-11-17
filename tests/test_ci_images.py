"""Tests for the CI image build plan (scripts/ci-images.py)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ci_images", ROOT / "scripts" / "ci-images.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def test_bases_come_first_and_files_exist():
    plan = ci.images("abc")
    seen = set()
    for image in plan:
        assert image.base is None or image.base in seen, f"{image.name} built before {image.base}"
        assert (ROOT / image.dockerfile).is_file(), image.dockerfile
        seen.add(image.name)
    # every pi tool layer is built and part of a variant
    tools = {d.name for d in (ROOT / ci.TOOL_LAYERS).iterdir() if (d / "Dockerfile").is_file()}
    assert tools <= {t for v in ci.VARIANTS for t in v.split(",")}


def test_only_adds_bases_and_push_command():
    names = [i.name for i in ci.with_bases(ci.images("abc"), ["asf-opencode-pnpm-python"])]
    assert names == ["scion-core-base", "scion-base", "asf-opencode", "asf-opencode-pnpm", "asf-opencode-pnpm-python"]
    image = next(i for i in ci.images("abc") if i.name == "asf-opencode-pnpm")
    argv = ci.commands(image, "ghcr.io/dewire", ["sha-1", "latest"], True, "linux/amd64,linux/arm64")
    assert "--push" in argv and "BASE_IMAGE=ghcr.io/dewire/asf-opencode:sha-1" in argv
    assert ["-t", "ghcr.io/dewire/asf-opencode-pnpm:latest"] == argv[argv.index("ghcr.io/dewire/asf-opencode-pnpm:latest") - 1:][:2]
    local = ci.commands(image, "ghcr.io/dewire", ["sha-1"], False, "linux/amd64")
    assert local[:2] == ["docker", "build"] and "--push" not in local
