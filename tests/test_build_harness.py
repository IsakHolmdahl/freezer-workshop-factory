"""Offline tests for build-harness.py: naming and config, no docker."""
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("build_harness", HERE / "build-harness.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class BuildHarnessTests(unittest.TestCase):
    def test_every_tool_declares_commands(self):
        tools = build.available_tools()
        self.assertEqual(tools["pnpm"], ["pnpm"])
        self.assertEqual(tools["python"], ["python", "pip", "pip3"])

    def test_selection_order_is_stable_and_unknown_tools_fail(self):
        tools = build.available_tools()
        self.assertEqual(build.select(["python", "pnpm", "pnpm"], tools), ["pnpm", "python"])
        self.assertEqual(build.select([], tools), [])
        with self.assertRaises(build.Failure):
            build.select(["gradle"], tools)

    def test_variant_config_sets_image_and_required_tools_only(self):
        text, image = build.base_config()
        variant = build.variant_config(text, "asf-opencode-pnpm:latest", ["pnpm", "node"])
        self.assertIn("\nimage: asf-opencode-pnpm:latest\n", "\n" + variant)
        line = next(l for l in variant.splitlines() if "required_image_tools" in l)
        self.assertTrue(line.rstrip().endswith("rtk, pnpm]"), line)
        self.assertEqual(variant.count("node"), text.count("node"))
        rest = lambda t: [l for l in t.splitlines() if not l.startswith("image:") and "required_image_tools" not in l]
        self.assertEqual(rest(variant), rest(text))

    def test_dry_run_names_variant_and_installs_under_that_name(self):
        out = subprocess.run([sys.executable, str(HERE / "build-harness.py"),
                              "--tools", "python,pnpm", "--dry-run"],
                             capture_output=True, text=True, check=True).stdout
        self.assertIn("asf-opencode-pnpm-python:", out)
        self.assertIn("--name asf-opencode-pnpm-python", out)
        self.assertIn("asf-opencode-pnpm:", out)


if __name__ == "__main__":
    unittest.main()
