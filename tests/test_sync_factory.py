"""Offline unit tests for scripts/sync-factory.py."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("sync_factory", HERE / "sync-factory.py")
sync_factory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync_factory)


class SyncFactoryTests(unittest.TestCase):
    def test_setup_binary_build_installs_shim(self):
        with patch.dict(sync_factory.os.environ), \
                patch.object(sync_factory.scion_source, "ensure_source"), \
                patch.object(sync_factory, "run") as run, \
                patch.object(sync_factory, "install_scion") as install:
            sync_factory.main(["--skip-build", "--skip-skills", "--skip-templates",
                               "--skip-harness-install", "--dolt-url", "",
                               "--install-path", "/tmp/asf-test/scion"])
        run.assert_called_once_with(["make", "-C", sync_factory.SCION_SRC_DIR, "all"], dry_run=False)
        install.assert_called_once_with(sync_factory.SCION_SRC_DIR / "build/scion",
                                        Path("/tmp/asf-test/scion"))

    def test_scion_cli_converts_template_paths_before_calling_adapter(self):
        adapter = Mock()
        adapter.scion.return_value.ok = True
        template = Path("/tmp/templates/architect")
        sync_factory.scion_cli(adapter, ["templates", "import", "--force", template])
        adapter.scion.assert_called_once_with(
            ["templates", "import", "--force", str(template)], timeout=600)

    def test_skills_semver_changes_when_skill_content_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "beads-work-tracking"
            skill.mkdir()
            (skill / "SKILL.md").write_text("v1")
            v1 = sync_factory.skills_semver(root)
            self.assertRegex(v1, r"^1\.0\.\d+$")
            self.assertEqual(v1, sync_factory.skills_semver(root))
            (skill / "SKILL.md").write_text("v2")
            v2 = sync_factory.skills_semver(root)
            self.assertNotEqual(v1, v2)
            scripts_dir = skill / "scripts"
            scripts_dir.mkdir()
            (scripts_dir / "helper.py").write_text("print(1)")
            self.assertNotEqual(v2, sync_factory.skills_semver(root))

    def test_selected_templates_includes_standard_roles(self):
        names = [d.name for d in sync_factory.selected_templates()]
        self.assertIn("coordinator", names)
        self.assertIn("developer", names)
        self.assertIn("code-reviewer", names)

    def test_dry_run_emits_build_publish_template_and_env_commands(self):
        out = subprocess.run(
            [sys.executable, str(HERE / "sync-factory.py"),
             "--variants", ";pnpm,python", "--dry-run"],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("build-harness.py --tools ", out)
        self.assertIn("build-harness.py --tools pnpm,python", out)
        self.assertIn("publish-skills-to-hub.sh --version 1.0.", out)
        self.assertIn("templates sync coordinator", out)
        self.assertIn("DOLT_URL mysql://beads@127.0.0.1:3307", out)
        self.assertIn("sync-hub-settings.py --hub", out)
        self.assertIn("--default-harness-config asf-opencode", out)
        self.assertLess(out.index("--tools pnpm,python"), out.index("sync-hub-settings.py"))

    def test_build_and_harness_install_can_be_split(self):
        def dry(*flags):
            return subprocess.run([sys.executable, str(HERE / "sync-factory.py"), "--variants", "pnpm",
                                   "--skip-skills", "--skip-templates", "--skip-binary", "--dolt-url", "",
                                   "--dry-run", *flags], capture_output=True, text=True, check=True).stdout
        build_only = dry("--skip-harness-install")
        self.assertIn("build-scion-base-image.py --tag-registry ", build_only)
        self.assertIn("--tools pnpm --base-image scion-local-base:latest --tag-registry ", build_only)
        self.assertIn("--no-install", build_only)
        self.assertNotIn("sync-hub-settings.py", build_only)
        install_only = dry("--skip-build")
        self.assertNotIn("build-scion-base-image.py", install_only)
        self.assertIn("--tools pnpm --no-build", install_only)
        self.assertNotIn("--no-install", install_only)
        self.assertNotIn("build-harness", dry("--skip-build", "--skip-harness-install"))

    def test_install_binary_replaces_a_running_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "new", Path(tmp) / "bin" / "scion"
            src.write_text("v2")
            dst.parent.mkdir()
            dst.write_text("v1")
            with open(dst):  # an open (or running) target must not block the install
                sync_factory.install_binary(src, dst)
            self.assertEqual(dst.read_text(), "v2")
            self.assertEqual(oct(dst.stat().st_mode)[-3:], "755")
            self.assertEqual(sorted(p.name for p in dst.parent.iterdir()), ["scion"])


if __name__ == "__main__":
    unittest.main()
