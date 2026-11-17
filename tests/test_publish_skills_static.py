"""Offline source contracts only: never execute the publisher or Scion CLI.

These assertions intentionally track shell source structure, not runtime behavior.
Run Bash's syntax check separately; live exit codes and rewrites remain untested.
"""

from pathlib import Path
import re
import unittest


PUBLISHER = Path(__file__).resolve().parents[1] / "scripts" / "publish-skills-to-hub.sh"


class PublishSkillsSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PUBLISHER.read_text(encoding="utf-8")
        cls.loop_start = cls.source.index('for skill_dir in "${SKILLS_DIR}"/*/; do')
        cls.loop_end = cls.source.index("\ndone", cls.loop_start) + len("\ndone")
        cls.loop = cls.source[cls.loop_start:cls.loop_end]

    def test_failures_accumulate_without_stopping_remaining_publications(self):
        self.assertIn("failed=0\n", self.source[:self.loop_start])
        publish_branch = re.search(
            r'if run scion_call skills publish [^\n]+; then\n'
            r'(?P<success>.*?)\n\s*else\n(?P<failure>.*?)\n\s*fi',
            self.loop,
            re.DOTALL,
        )
        self.assertIsNotNone(publish_branch)
        self.assertIn('published_names+=("${name}")', publish_branch["success"])
        self.assertIn('echo "  failed to publish ${name}" >&2', publish_branch["failure"])
        self.assertIn("failed=1", publish_branch["failure"])
        self.assertNotRegex(publish_branch["failure"], r"\b(exit|break|return)\b")
        self.assertNotIn("failed=0", self.loop)

    def test_failure_exit_precedes_rewrite_and_all_success_exits(self):
        after_loop = self.source[self.loop_end:]
        guard = re.search(
            r'if \[\[ "\$\{failed\}" -ne 0 \]\]; then\n'
            r'(?P<body>.*?)\nfi',
            after_loop,
            re.DOTALL,
        )
        self.assertIsNotNone(guard)
        self.assertIn("exit 1", guard["body"])
        self.assertIn(
            'echo "error: one or more skills failed to publish; skipping template rewrite" >&2',
            guard["body"],
        )
        for marker in (
            'if [[ "${REWRITE}" -ne 1 ]]',
            'if [[ ! -d "${TEMPLATES_DIR}" ]]',
            "exit 0",
            'sed -i -E "s#${pattern}#${replacement}#g" "${file}"',
            'echo "Done."',
        ):
            with self.subTest(marker=marker):
                self.assertLess(guard.end(), after_loop.index(marker))

    def test_existing_versions_still_count_as_success(self):
        idempotent_branch = self.loop.split("\n\tif run scion_call skills publish", 1)[0]
        self.assertIn('scion_call skills versions "${name}" --format json', idempotent_branch)
        self.assertIn('published_names+=("${name}")', idempotent_branch)
        self.assertIn("\n\t\tcontinue\n", idempotent_branch)
        self.assertNotIn("failed=1", idempotent_branch)

    def test_success_path_keeps_rewrite_and_completion(self):
        after_loop = self.source[self.loop_end:]
        guard_end = after_loop.index("\nfi") + len("\nfi")
        continuation = after_loop[guard_end:]
        self.assertNotIn("failed=", continuation)
        self.assertIn('for name in "${published_names[@]}"; do', continuation)
        self.assertIn('replacement="uri: \\"skill://scion/global/${name}\\""', continuation)
        self.assertTrue(continuation.rstrip().endswith('echo "Done."'))


if __name__ == "__main__":
    unittest.main()
