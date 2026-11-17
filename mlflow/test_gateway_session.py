"""Run with the MLflow image and bootstrap on PYTHONPATH; no model calls needed."""

import subprocess
import sys
import types
import unittest

import gateway_session


class Request:
    def __init__(self, headers):
        self.headers = headers


class GatewaySessionTests(unittest.TestCase):
    def test_valid_headers_become_trace_metadata(self):
        module = types.SimpleNamespace(_get_user_metadata=lambda request: {"existing": "kept"})
        gateway_session.patch(module)
        gateway_session.patch(module)  # Idempotent: wrapped once.
        metadata = module._get_user_metadata(Request({
            "x-mlflow-session-id": "1b4e28ba-2fa1-11d2-883f-0016d3cca427",
            "x-mlflow-trace-user": "mayor-auth",
        }))
        self.assertEqual(metadata, {
            "existing": "kept",
            "mlflow.trace.session": "1b4e28ba-2fa1-11d2-883f-0016d3cca427",
            "mlflow.trace.user": "mayor-auth",
        })

    def test_missing_or_unsafe_values_are_ignored(self):
        self.assertEqual(gateway_session.session_metadata({}), {})
        for bad in ("", "has space", "x" * 200, "<script>", "-leading"):
            self.assertEqual(gateway_session.session_metadata({"x-mlflow-session-id": bad}), {})

    def test_real_gateway_module_is_patched_on_import(self):
        subprocess.run([sys.executable, "-c",
            "import mlflow.server.gateway_api as g; "
            "assert getattr(g._get_user_metadata, '_gateway_session', False)"], check=True)

    def test_assistant_module_localhost_check_is_patched(self):
        module = types.SimpleNamespace(_is_localhost=lambda request: False)
        gateway_session.patch_assistant(module)
        self.assertTrue(module._is_localhost(Request({})))

    def test_openai_compatible_sparse_tool_call_index_is_dense(self):
        module = types.SimpleNamespace()
        gateway_session.patch_openai_compatible(module)
        acc = []
        # Anthropic/Copilot emits index=1 when text was block 0
        module._merge_tool_call_chunk(acc, {"index": 1, "id": "toolu_1", "function": {"name": "Bash", "arguments": ""}})
        module._merge_tool_call_chunk(acc, {"index": 1, "id": None, "function": {"name": None, "arguments": '{"command": "ls"}'}})
        self.assertEqual(len(acc), 1)
        self.assertEqual(acc[0]["id"], "toolu_1")
        self.assertEqual(acc[0]["function"]["name"], "Bash")
        self.assertEqual(acc[0]["function"]["arguments"], '{"command": "ls"}')


if __name__ == "__main__":
    unittest.main()
