#!/usr/bin/env python3
"""Unit tests for asf-entrypoint.py: python3 -m unittest asf_entrypoint_test."""
import importlib.util
import tempfile
import unittest
import unittest.mock
from pathlib import Path

spec = importlib.util.spec_from_file_location("asf_entrypoint", Path(__file__).with_name("asf-entrypoint.py"))
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class PrepareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.base = {"SCION_SHARED_DIR_PATHS": f"other=/x,scratchpad={self.tmp}",
                     "SCION_AGENT_ID": "agent-1", "SCION_AGENT_NAME": "sf-ab12-login-developer"}

    def test_scratchpad_env_and_bead_id(self):
        env = entry.prepare(dict(self.base))
        self.assertEqual(env["BEADS_DIR"], str(self.tmp / "beads"))
        # no build-cache shared dir: falls back to scratchpad/cache
        self.assertEqual(env["ASF_BUILD_CACHE"], str(self.tmp / "cache"))
        self.assertEqual(env["BEADS_ISSUE_ID"], "sf-ab12")
        self.assertEqual(env["BEADS_ACTOR"], "sf-ab12-login-developer")
        self.assertTrue((self.tmp / "cache/pnpm-store").is_dir())

    def test_build_cache_shared_dir(self):
        cache = Path(tempfile.mkdtemp())
        paths = f"scratchpad={self.tmp},build-cache={cache}"
        env = entry.prepare({**self.base, "SCION_SHARED_DIR_PATHS": paths, "PIP_CACHE_DIR": "/mine"})
        self.assertEqual(env["GRADLE_USER_HOME"], str(cache / "gradle"))
        self.assertEqual(env["NPM_CONFIG_STORE_DIR"], str(cache / "pnpm-store"))
        self.assertEqual(env["PIP_CACHE_DIR"], "/mine")  # explicit values win
        self.assertEqual(env["ASF_BUILD_CACHE"], str(cache))

    def test_trace_root_shares_its_trace_id(self):
        root = entry.prepare({**self.base, "ASF_TRACE_ROOT": "true"})
        worker = entry.prepare({**self.base, "SCION_AGENT_ID": "agent-2"})
        self.assertEqual(root["TRACE_ID"], "agent-1")
        self.assertEqual(worker["TRACE_ID"], "agent-1")

    def test_existing_dolt_target_is_exported(self):
        (self.tmp / "beads").mkdir()
        (self.tmp / "beads/metadata.json").write_text("{}")
        (self.tmp / "beads/dolt-target.json").write_text(
            '{"host": "127.0.0.1", "port": 3307, "user": "beads", "database": "beads_x"}')
        (self.tmp / "beads/.env").write_text("BEADS_DOLT_PASSWORD=pw\n")
        env = entry.prepare({**self.base, "DOLT_URL": "mysql://beads@127.0.0.1:3307", "DOLT_DB_PASSWORD": "pw",
                             "SCION_PROJECT_ID": "2e8ee186-e41a-4cd7-8f2b-c70ae2351b01",
                             "SCION_PROJECT": "order-service-api"})
        self.assertEqual(env["BEADS_DOLT_SERVER_DATABASE"], "beads_x")  # existing target is kept
        self.assertEqual(env["BD_NO_PUSH"], "true")

    def test_without_scratchpad(self):
        env = entry.prepare({"SCION_AGENT_ID": "a"})
        self.assertNotIn("BEADS_DIR", env)
        self.assertEqual(env["TRACE_ID"], "a")

    def test_testcontainers_socket_sets_docker_env(self):
        import socket as sockmod
        sock_path = self.tmp / "docker.sock"
        srv = sockmod.socket(sockmod.AF_UNIX, sockmod.SOCK_STREAM)
        self.addCleanup(srv.close)
        srv.bind(str(sock_path))
        env: dict[str, str] = {}
        entry.setup_testcontainers(env, sock_path)
        self.assertEqual(env["DOCKER_HOST"], f"unix://{sock_path}")
        self.assertEqual(env["TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE"], str(sock_path))
        self.assertEqual(env["TESTCONTAINERS_HOST_OVERRIDE"], "127.0.0.1")

    def test_preserves_git_author_identity(self):
        env = {"ASF_GIT_AUTHOR_NAME": "Jane Dev", "SCION_CREATOR": "jane@example.com"}
        calls = []
        with unittest.mock.patch.object(entry.subprocess, "run", lambda argv, **kw: calls.append(argv)):
            entry.preserve_git_author(env)
        self.assertEqual(env["GIT_AUTHOR_NAME"], "Jane Dev")
        self.assertEqual(env["GIT_COMMITTER_NAME"], "Jane Dev")
        self.assertEqual(env["GIT_AUTHOR_EMAIL"], "jane@example.com")
        self.assertEqual(env["GIT_COMMITTER_EMAIL"], "jane@example.com")
        self.assertIn(["git", "config", "--global", "user.name", "Jane Dev"], calls)
        self.assertIn(["git", "config", "--global", "user.email", "jane@example.com"], calls)


class NamingTest(unittest.TestCase):
    def test_database_and_prefix_from_project_slug(self):
        env = {"SCION_PROJECT": "order-service-api", "SCION_PROJECT_ID": "2e8ee186-e41a-4cd7-8f2b-c70ae2351b01"}
        self.assertEqual(entry.database_name(env), "beads_order_service_api")
        self.assertEqual(entry.issue_prefix(env), "osa")
        self.assertEqual(entry.issue_prefix({"SCION_PROJECT": "order-service-spring-parent"}), "ossp")
        self.assertEqual(entry.issue_prefix({"SCION_PROJECT": "ai-software-factory"}), "asf")
        self.assertEqual(entry.issue_prefix({"SCION_PROJECT": "My_App"}), "my-app")
        self.assertEqual(entry.issue_prefix({"SCION_PROJECT": "monolithservice"}), "mono")
        self.assertEqual(entry.issue_prefix({**env, "BEADS_PREFIX": "sf"}), "sf")
        self.assertEqual(entry.database_name({"SCION_PROJECT_ID": env["SCION_PROJECT_ID"]}),
                         "beads_2e8ee186e41a4cd78f2bc70ae2351b01")

    def test_bead_id_from_agent_name(self):
        name = entry.bead_id_from_agent_name
        self.assertEqual(name("osa-a1b-login-developer", "osa"), "osa-a1b")
        self.assertEqual(name("my-app-a1b.2-fix1-developer", "my\\-app"), "my-app-a1b.2")
        self.assertEqual(name("sf-ab12-login-developer", "osa"), "sf-ab12")  # legacy prefix
        self.assertEqual(name("coordinator", "x"), "")

    def test_new_project_is_initialised_with_slug_names(self):
        beads = Path(tempfile.mkdtemp())
        calls = []
        ok = lambda argv, **kw: calls.append(argv) or entry.subprocess.CompletedProcess(argv, 0, "")
        env = {"DOLT_URL": "mysql://beads@127.0.0.1:3307", "DOLT_DB_PASSWORD": "pw", "HOME": str(beads),
               "SCION_PROJECT_ID": "2e8ee186-e41a-4cd7-8f2b-c70ae2351b01", "SCION_PROJECT": "order-service-api"}
        with unittest.mock.patch.object(entry.subprocess, "run", ok):
            entry.init_beads(env, beads)
        self.assertIn("CREATE DATABASE IF NOT EXISTS `beads_order_service_api`;", calls[0])
        self.assertIn("--prefix", calls[1])
        self.assertEqual(calls[1][calls[1].index("--prefix") + 1], "osa")
        self.assertIn(["git", "config", "--global", "beads.role", "maintainer"], calls)
        self.assertEqual(env["BEADS_DOLT_SERVER_DATABASE"], "beads_order_service_api")


class ExtraPackagesTest(unittest.TestCase):
    def test_block_has_tools_memories_and_is_replaced(self):
        home = Path(tempfile.mkdtemp())
        tools = Path(tempfile.mkdtemp())
        (tools / "pnpm.md").write_text("### Node and pnpm\n- pnpm 10")
        agents = home / ".config/opencode/AGENTS.md"
        agents.parent.mkdir(parents=True)
        agents.write_text("role instructions\n")
        outputs = {"bd": "use pnpm for svc-a [pnpm-svc-a]"}
        fake = lambda argv, env, cwd=None: outputs[argv[0]]
        env = {"HOME": str(home), "BEADS_DIR": "/x", "SCION_AGENT_WORKSPACE": "/nonexistent",
               "SCION_CREATOR": "jeger@example.com"}
        with unittest.mock.patch.object(entry, "TOOLS_DIR", tools), unittest.mock.patch.object(entry, "_run", fake):
            entry.write_start_context(env)
            outputs["bd"] = "second"
            entry.write_start_context(env)
        text = agents.read_text()
        self.assertTrue(text.startswith(entry.CONTEXT_START))  # block first, role instructions after
        self.assertTrue(text.rstrip().endswith("role instructions"))
        self.assertEqual(text.count(entry.CONTEXT_START), 1)
        self.assertIn("pnpm 10", text)
        self.assertIn("second", text)
        self.assertNotIn("svc-a", text)
        self.assertIn("bd remember", text)
        self.assertLess(text.index("your terminal is never read"), text.index("Build tools"))
        self.assertIn("Report to: `@jeger@example.com`", text)
        self.assertEqual(entry.report_to({"SCION_CREATOR": "coord-1"}), "agent:coord-1")


if __name__ == "__main__":
    unittest.main()
