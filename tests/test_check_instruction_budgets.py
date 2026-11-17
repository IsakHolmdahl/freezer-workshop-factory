#!/usr/bin/env python3
"""Offline tests for check-instruction-budgets.check_template (temp dirs only)."""

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check-instruction-budgets.py"
spec = importlib.util.spec_from_file_location("check_instruction_budgets", SCRIPT)
budgets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(budgets)

HANDOFF = ("## Completion Handoff agent:<dispatcher> --body-file exit status retry once "
           "Completion delivery failed status blocked status task_completed sleep poll")


class CheckTemplateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def make(self, name, agents="Short role text.", prompt="Be brief.", yaml_text="schema_version: '1'\n"):
        d = self.root / name
        d.mkdir()
        (d / "agents.md").write_text(agents)
        (d / "system-prompt.md").write_text(prompt)
        (d / "scion-agent.yaml").write_text(yaml_text)
        return d

    def test_allows_large_instructions_and_prompts(self):
        d = self.make("my-agent", agents=HANDOFF + " w" * 2000, prompt="w " * 500)
        self.assertEqual(budgets.check_template(d), [])

    def test_held_to_handoff_strings(self):
        d = self.make("my-agent")
        errors = budgets.check_template(d)
        self.assertIn("my-agent: missing ## Completion Handoff", errors)
        self.assertIn("my-agent: missing waiting rule", errors)

    def test_with_handoff_passes(self):
        d = self.make("my-agent", agents=HANDOFF)
        self.assertEqual(budgets.check_template(d), [])

    def test_harness_selection_stays_out_of_templates(self):
        for key in ("harness", "harness_config", "default_harness_config", "default-harness-config"):
            with self.subTest(key=key):
                d = self.make(key, agents=HANDOFF, yaml_text=f"{key}: asf-opencode\n")
                self.assertIn(f"{key} belongs in settings or --harness-config",
                              "\n".join(budgets.check_template(d)))

    def test_yaml_selected_nested_files_are_checked(self):
        d = self.make("selected", agents="w " * 501, prompt="w " * 101,
                      yaml_text="agent_instructions: notes/role.md\nsystem_prompt: notes/prompt.md\n")
        (d / "notes").mkdir()
        (d / "notes/role.md").write_text(HANDOFF + " w" * 1000)
        (d / "notes/prompt.md").write_text("w " * 300)
        self.assertEqual(budgets.check_template(d), [])

    def test_selected_instructions_supply_lifecycle_checks(self):
        d = self.make("plain", agents=HANDOFF,
                      yaml_text="agent_instructions: other.md\n")
        (d / "other.md").write_text("No handoff here.")
        self.assertIn("plain: missing ## Completion Handoff", budgets.check_template(d))
        (d / "other.md").write_text(HANDOFF)
        (d / "agents.md").unlink()
        self.assertEqual(budgets.check_template(d), [])

    def test_missing_and_directory_instruction_files_return_errors(self):
        for key in ("agent_instructions", "system_prompt"):
            for target in ("missing.md", "notes"):
                with self.subTest(key=key, target=target):
                    d = self.make(f"t-{key}-{target}", yaml_text=f"{key}: {target}\n")
                    (d / "notes").mkdir()
                    errors = budgets.check_template(d)
                    self.assertEqual(len(errors), 1)
                    self.assertIn(key, errors[0])
                    self.assertIn(target, errors[0])
                    self.assertIn("readable regular file", errors[0])

    def test_missing_legacy_instruction_files_return_errors(self):
        d = self.make("t-legacy")
        (d / "agents.md").unlink()
        (d / "system-prompt.md").unlink()
        errors = budgets.check_template(d)
        self.assertEqual(len(errors), 2)
        self.assertIn("agent_instructions", errors[0])
        self.assertIn("system_prompt", errors[1])

    def test_instruction_fields_reject_malformed_types_without_fallback(self):
        for key in ("agent_instructions", "system_prompt"):
            for value in ("[]", "null", "{}", "42", "true", "''"):
                with self.subTest(key=key, value=value):
                    d = self.make(f"t-{key}-{value}", yaml_text=f"{key}: {value}\n")
                    errors = budgets.check_template(d)
                    self.assertEqual(len(errors), 1)
                    self.assertIn(f"scion-agent.yaml: {key}", errors[0])
                    self.assertIn("nonempty relative path", errors[0])

    def test_paths_outside_template_and_symlink_escapes_are_rejected(self):
        outside = self.root / "outside.md"
        outside.write_text("Brief.")
        for key in ("agent_instructions", "system_prompt"):
            for index, target in enumerate((str(outside), "../outside.md", "escape.md")):
                with self.subTest(key=key, target=target):
                    d = self.make(f"t-{key}-{index}", yaml_text=f"{key}: {target}\n")
                    (d / "escape.md").symlink_to(outside)
                    errors = budgets.check_template(d)
                    self.assertEqual(len(errors), 1)
                    self.assertIn(key, errors[0])
                    self.assertIn("template root", errors[0])

    def test_symlink_directory_escape_and_loop_return_errors(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "role.md").write_text("Brief.")
        for index, target in enumerate(("linked/role.md", "loop.md")):
            with self.subTest(target=target):
                d = self.make(f"t-links-{index}",
                              yaml_text=f"agent_instructions: {target}\n")
                (d / "linked").symlink_to(outside, target_is_directory=True)
                (d / "loop.md").symlink_to("loop.md")
                errors = budgets.check_template(d)
                self.assertEqual(len(errors), 1)
                self.assertIn("agent_instructions", errors[0])
                self.assertIn("template root", errors[0])

    def test_main_preserves_normal_output_and_total(self):
        templates = self.root / "templates"
        templates.mkdir()
        d = self.make("output", agents=HANDOFF, prompt="Be brief.")
        d.rename(templates / d.name)
        out = io.StringIO()
        with patch.object(budgets, "ROOT", self.root), contextlib.redirect_stdout(out):
            budgets.main()
        self.assertEqual(out.getvalue(),
                         "output: agents=18, system=2 words\n"
                         "Total: 20 words (word counts, not tokenizer measurements)\n")

    def test_internal_symlink_is_supported(self):
        d = self.make("link", agents=HANDOFF, yaml_text="agent_instructions: alias.md\n")
        (d / "alias.md").symlink_to("agents.md")
        self.assertEqual(budgets.check_template(d), [])

    def test_unreadable_instruction_returns_error(self):
        d = self.make("t-unreadable")
        (d / "agents.md").chmod(0)
        self.addCleanup((d / "agents.md").chmod, 0o600)
        errors = budgets.check_template(d)
        self.assertEqual(len(errors), 1)
        self.assertIn("agent_instructions", errors[0])
        self.assertIn("readable regular file", errors[0])

    def test_missing_directory_and_malformed_yaml_return_diagnostics(self):
        for index, text in enumerate(("[broken", "[]", "null", "42")):
            with self.subTest(text=text):
                d = self.make(f"t-yaml-{index}", yaml_text=text)
                errors = budgets.check_template(d)
                self.assertEqual(len(errors), 1)
                self.assertIn("scion-agent.yaml", errors[0])
                self.assertIn("YAML", errors[0])
        d = self.make("t-missing-yaml")
        (d / "scion-agent.yaml").unlink()
        self.assertIn("scion-agent.yaml", budgets.check_template(d)[0])
        (d / "scion-agent.yaml").mkdir()
        self.assertIn("readable regular file", budgets.check_template(d)[0])


if __name__ == "__main__":
    unittest.main()
