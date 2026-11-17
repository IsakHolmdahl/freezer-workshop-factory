#!/usr/bin/env python3
"""Unit tests for the ASF OpenCode provisioner: python3 -m unittest provision_test (from this directory)."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import provision
import scion_harness as sh

ROOT = Path(__file__).resolve().parent
ALIASES = {"small": "gateway/small", "medium": "gateway/medium", "large": "gateway/large",
           "extra-large": "gateway/large"}


class FakeContext:
    harness_name = "asf-opencode"

    def __init__(self, home, secrets=None, system_prompt="", instructions=""):
        self.home = home
        self.bundle_dir = os.path.join(home, "no-bundle")
        self.harness_config = {"config_dir": ".config/opencode",
                               "instructions_file": ".config/opencode/AGENTS.md",
                               "system_prompt_file": ".config/opencode/system-prompt.md",
                               "model_aliases": ALIASES}
        self.secrets = secrets or {}
        self.system_prompt = system_prompt
        self.instructions = instructions
        self.outputs = None

    def read_secret(self, name, env_fallback=False):
        return self.secrets.get(name, "")

    def read_input_text(self, name):
        return {"system-prompt.md": self.system_prompt, "instructions.md": self.instructions}.get(name, "")

    def select_auth(self, spec):
        key = self.secrets.get("AGENT_GATEWAY_API_KEY")
        return (sh.ResolvedAuth(method="api-key", env_key="AGENT_GATEWAY_API_KEY") if key
                else sh.ResolvedAuth(method="none"))

    def write_outputs(self, resolved, env=None, extra=None):
        self.outputs = env

    def info(self, message):
        pass

    def warn(self, message):
        pass


class ProvisionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = Path(self.tmp) / "home"
        # The agent home starts as a copy of the bundle's home/ (theme, plugins, launch script).
        shutil.copytree(ROOT / "home", self.home)
        self.config_file = self.home / ".config/opencode/opencode.json"
        for name in ("_mlflow_endpoints",):
            patcher = patch.object(provision, name, lambda ctx, url, key: ["large", "extra"])
            patcher.start()
            self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("SCION_MODEL", None)

    def run_provision(self, secrets=None, **kwargs):
        ctx = FakeContext(str(self.home), secrets, **kwargs)
        provision.provision(ctx)
        return ctx, json.loads(self.config_file.read_text())

    def test_keyless_local_mlflow_gateway(self):
        ctx, config = self.run_provision()
        gateway = config["provider"]["gateway"]
        self.assertEqual(gateway["npm"], "@ai-sdk/openai-compatible")
        self.assertEqual(gateway["options"]["baseURL"], provision.DEFAULT_GATEWAY_URL)
        # The key is an env reference, never the value.
        self.assertEqual(gateway["options"]["apiKey"], "{env:AGENT_GATEWAY_API_KEY}")
        self.assertEqual(gateway["options"]["headers"]["X-MLflow-Session-Id"], "{env:TRACE_ID}")
        self.assertEqual(sorted(gateway["models"]), ["extra", "large", "medium", "small"])
        self.assertEqual(ctx.outputs["AGENT_GATEWAY_API_KEY"], "none")
        self.assertEqual(ctx.outputs["SCION_USAGE_SOURCE"], "hooks")
        self.assertEqual(config["theme"], "matrix")  # existing settings are kept

    def test_other_gateway_without_mlflow_extras(self):
        ctx, config = self.run_provision({"AGENT_GATEWAY_URL": "https://llm.example.com/v1/",
                                          "AGENT_GATEWAY_TYPE": "openai", "AGENT_GATEWAY_API_KEY": "secret"})
        gateway = config["provider"]["gateway"]
        self.assertEqual(gateway["options"]["baseURL"], "https://llm.example.com/v1")
        self.assertNotIn("headers", gateway["options"])
        self.assertEqual(sorted(gateway["models"]), ["large", "medium", "small"])
        self.assertNotIn("secret", self.config_file.read_text())
        self.assertEqual(ctx.outputs["AGENT_GATEWAY_API_KEY"], "secret")

    def test_unsafe_urls_are_rejected(self):
        for url, secrets in (("http://llm.example.com/v1", {"AGENT_GATEWAY_API_KEY": "k"}),
                             ("https://llm.example.com/v1", {}),
                             ("https://user:pw@llm.example.com/v1", {"AGENT_GATEWAY_API_KEY": "k"}),
                             ("ftp://localhost/v1", {})):
            with self.subTest(url=url), self.assertRaises(sh.ProvisionError):
                self.run_provision({"AGENT_GATEWAY_URL": url, **secrets})

    def test_model_aliases_resolve_to_gateway_models(self):
        for requested, expected in (("small", "gateway/small"), ("extra-large", "gateway/large"),
                                    ("gateway/medium", "gateway/medium"), ("", "gateway/medium")):
            with self.subTest(requested=requested):
                os.environ["SCION_MODEL"] = requested
                _, config = self.run_provision()
                self.assertEqual(config["model"], expected)
                self.assertEqual(config["small_model"], "gateway/small")
        os.environ["SCION_MODEL"] = "anthropic/claude-opus"
        with self.assertRaises(sh.ProvisionError):
            self.run_provision()

    def test_only_the_gateway_provider_is_usable(self):
        _, config = self.run_provision()
        self.assertIn("github-copilot", config["disabled_providers"])
        self.assertIn("opencode", config["disabled_providers"])
        self.assertFalse(config["autoupdate"])
        self.assertEqual(config["permission"]["bash"], "allow")

    def test_context_window_sets_model_limits(self):
        _, config = self.run_provision({"ASF_CONTEXT_WINDOW": "64000"})
        self.assertEqual(config["provider"]["gateway"]["models"]["small"]["limit"],
                         {"context": 64000, "output": 16000})
        self.config_file.unlink()
        _, config = self.run_provision({"ASF_CONTEXT_WINDOW": "0"})
        self.assertNotIn("limit", config["provider"]["gateway"]["models"]["small"])

    def test_user_settings_are_kept(self):
        self.config_file.write_text(json.dumps({"theme": "mine", "permission": {"bash": "ask"},
                                                "provider": {"gateway": {"models": {"small": {"name": "Tiny"}}}}}))
        _, config = self.run_provision()
        self.assertEqual(config["theme"], "mine")
        self.assertEqual(config["permission"]["bash"], "ask")
        self.assertEqual(config["provider"]["gateway"]["models"]["small"]["name"], "Tiny")

    def test_config_with_comments_is_an_error(self):
        self.config_file.write_text('{"theme": "x"} // comment')
        with self.assertRaises(sh.ProvisionError):
            self.run_provision()

    def test_system_prompt_replaces_the_primary_agent_prompt(self):
        prompt_file = self.home / ".config/opencode/system-prompt.md"
        _, config = self.run_provision(system_prompt="You coordinate.")
        self.assertEqual(prompt_file.read_text(), "You coordinate.")
        self.assertEqual(config["agent"]["build"]["prompt"], "{file:" + str(prompt_file) + "}")
        _, config = self.run_provision()
        self.assertFalse(prompt_file.exists())
        self.assertNotIn("agent", config)

    def test_instructions_go_to_global_agents_md_without_the_system_prompt(self):
        self.run_provision(system_prompt="You coordinate.", instructions="Use bd for work records.")
        text = (self.home / ".config/opencode/AGENTS.md").read_text()
        self.assertIn("Use bd for work records.", text)
        self.assertNotIn("You coordinate.", text)

    def test_mcp_translation(self):
        self.assertEqual(provision._translate_mcp_server("a", {"transport": "stdio", "command": "x", "args": [1],
                                                               "env": {"K": "v"}}),
                         {"type": "local", "command": ["x", "1"], "environment": {"K": "v"}})
        self.assertEqual(provision._translate_mcp_server("b", {"transport": "streamable-http", "url": "https://m"}),
                         {"type": "remote", "url": "https://m"})
        self.assertIsNone(provision._translate_mcp_server("c", {"transport": "carrier-pigeon"}))

    def test_config_file_is_private(self):
        self.run_provision()
        self.assertEqual(self.config_file.stat().st_mode & 0o777, 0o600)

    def test_vendored_library_is_the_upstream_copy(self):
        upstream = ROOT.parents[1] / "scion" / "harnesses"
        if not (upstream / "scion_harness.py").is_file():
            self.skipTest("scion submodule not checked out")
        self.assertEqual((ROOT / "scion_harness.py").read_bytes(), (upstream / "scion_harness.py").read_bytes(),
                         "copy scion/harnesses/scion_harness.py into harness-configs/asf-opencode/")

    def test_upstream_bridge_files_are_unchanged(self):
        """The hook bridge, dialect and launch wrapper are upstream's; update them with the submodule."""
        upstream = ROOT.parents[1] / "scion" / "harnesses" / "opencode"
        if not upstream.is_dir():
            self.skipTest("scion submodule not checked out")
        for name in ("dialect.yaml", "home/.config/opencode/plugins/scion-bridge.js",
                     "home/.local/bin/opencode-launch.sh"):
            with self.subTest(name=name):
                self.assertEqual((ROOT / name).read_bytes(), (upstream / name).read_bytes(),
                                 f"copy scion/harnesses/opencode/{name} into harness-configs/asf-opencode/")


if __name__ == "__main__":
    unittest.main()
