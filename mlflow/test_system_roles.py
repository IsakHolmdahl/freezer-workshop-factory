"""Run with the MLflow image and bootstrap on PYTHONPATH; no model calls needed."""

import copy
import subprocess
import sys
import unittest

import litellm
from litellm.llms.github_copilot.chat.transformation import GithubCopilotConfig


class CopilotSystemRoleTests(unittest.TestCase):
    def test_bootstrap_loaded_in_fresh_worker(self):
        subprocess.run(
            [sys.executable, "-c",
             "import litellm; assert litellm.disable_copilot_system_to_assistant is True"],
            check=True,
        )

    def test_adapter_preserves_roles_content_and_tools(self):
        self.assertIs(litellm.disable_copilot_system_to_assistant, True)
        messages = [
            {"role": "system", "content": "Follow the role instructions."},
            {"role": "user", "content": "Redesign the UI"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "dispatch", "type": "function",
                "function": {"name": "bash", "arguments": '{"command":"scion list"}'},
            }]},
            {"role": "tool", "tool_call_id": "dispatch", "content": "[]"},
        ]
        original = copy.deepcopy(messages)
        adapter = GithubCopilotConfig()
        for model in ("claude-sonnet-4", "gemini-2.5-pro"):
            with self.subTest(model=model):
                request = adapter.transform_request(model, messages, {"stream": True}, {}, {})
                self.assertEqual(request["messages"], original)
                self.assertEqual(messages, original)
                self.assertTrue(request["stream"])
                self.assertEqual(request["model"], model)


if __name__ == "__main__":
    unittest.main()
