#!/usr/bin/env python3
"""Container entrypoint for OpenCode agents: prepare the Beads/trace environment, then exec the agent.

Runs after sciontool init (and so after provision.py), right before the agent
command. Agent-specific behaviour is driven by template env, not role names:

  ASF_TRACE_ROOT=true     this agent starts the project's MLflow trace session
                          (coordinators); others in the project join it.
  BEADS_PREFIX=...        issue ID prefix when the project's Beads database is
                          created (default: the project slug, e.g. order-service-api-a1b).

Every start also writes a block into ~/.config/opencode/AGENTS.md with the rule that
nobody reads the terminal, the image's build tools (/etc/scion-tools/*.md) and the
project's `bd memories`, plus the rule to record tooling fixes with `bd remember`.

Shared dirs: `scratchpad` holds BEADS_DIR and the trace ID; `build-cache`
(both created for every project via ~/.scion/settings.yaml shared_dirs) holds
the Gradle home, pnpm store, npm/pip/uv caches, shared by all
agents of the project. Without build-cache, scratchpad/cache is used.

Beads: the project's shared scratchpad holds BEADS_DIR. With DOLT_URL and
DOLT_DB_PASSWORD set, the first agent creates the project database
(beads_<project-slug>) on the shared Dolt server and runs `bd init`; every agent
gets the BEADS_DOLT_* connection variables.
"""
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

BD_DEFAULTS = {
    "BEADS_DOLT_AUTO_START": "0",
    "BD_BACKUP_ENABLED": "false",
    "BD_BACKUP_GIT_PUSH": "false",
    "BD_DOLT_AUTO_PUSH": "false",
    "BD_NO_GIT_OPS": "true",
    "BD_NO_PUSH": "true",
}
# Agent names follow `<bead-id>-<short-topic>-<role>` (beads-orchestration skill).
BEAD_ID_RE = re.compile(r"^([a-z]{2,8}-[a-z0-9]{2,12}(?:\.[0-9]+)?)-[a-z0-9-]+$")


def shared_dir(env, name):
    """Container path of the project shared dir `name`, or None when not mounted."""
    for entry in env.get("SCION_SHARED_DIR_PATHS", "").split(","):
        if entry.startswith(f"{name}=/"):
            return Path(entry.split("=", 1)[1])
    for candidate in (Path("/scion-volumes") / name,
                      Path(env.get("SCION_AGENT_WORKSPACE", "/nonexistent")) / ".scion-volumes" / name):
        if candidate.is_dir():
            return candidate
    return None


# Build caches shared by all agents of a project: env var -> subdirectory.
CACHE_DIRS = {
    "GRADLE_USER_HOME": "gradle",
    "NPM_CONFIG_STORE_DIR": "pnpm-store",
    "npm_config_cache": "npm",
    "PIP_CACHE_DIR": "pip",
    "UV_CACHE_DIR": "uv",
}


def setup_build_cache(env, cache):
    """Point build tools at the shared build-cache dir (keeps values set elsewhere)."""
    try:
        for sub in CACHE_DIRS.values():
            (cache / sub).mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"asf-entrypoint: build cache not writable: {error}", file=sys.stderr)
        return
    for key, sub in CACHE_DIRS.items():
        setdefault(env, key, str(cache / sub))
    env["ASF_BUILD_CACHE"] = str(cache)


def setdefault(env, key, value):
    if value and not env.get(key):
        env[key] = value


def write_private(path, text):
    path.write_text(text)
    path.chmod(0o600)


def database_name(env):
    """beads_<project slug>, e.g. beads_order_service_api (MySQL identifier, max 64 chars).

    Falls back to the project UUID when the slug is unknown.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", env.get("SCION_PROJECT", "").lower()).strip("_")
    return "beads_" + (slug[:58] if slug else uuid.UUID(env["SCION_PROJECT_ID"]).hex)


def issue_prefix(env):
    """BEADS_PREFIX if set, else an abbreviated project slug (<=8 chars):
    short slugs stay as-is (asf, my-app), longer multi-word slugs take the
    initials of each word (order-service-api -> osa, ai-software-factory -> asf,
    order-service-spring-parent -> ossp), and single long words take their first
    4 chars."""
    if env.get("BEADS_PREFIX"):
        return env["BEADS_PREFIX"]
    parts = [p for p in re.split(r"[^a-z0-9]+", env.get("SCION_PROJECT", "").lower()) if p]
    if not parts:
        return "bd"
    joined = "-".join(parts)
    if len(joined) <= 8:
        return joined
    if len(parts) >= 2:
        return "".join(p[0] for p in parts)[:8]
    return parts[0][:4]


def bead_id_from_agent_name(name, prefix):
    """`<bead-id>-<topic>-<role>` -> bead id; the prefix may itself contain dashes."""
    for candidate in (prefix, r"[a-z]{2,8}"):  # this project's prefix, else a short legacy one (sf)
        match = re.match(rf"^({candidate}-[a-z0-9]{{2,12}}(?:\.[0-9]+)*)-[a-z0-9-]+$", name)
        if match:
            return match.group(1)
    return ""


def init_beads(env, beads):
    """Create the project database and bd workspace once; return the Dolt target.

    An existing dolt-target.json is kept: projects created before the slug-based
    names keep their database.
    """
    target_file = beads / "dolt-target.json"
    url, password, project = env.get("DOLT_URL", ""), env.get("DOLT_DB_PASSWORD", ""), env.get("SCION_PROJECT_ID", "")
    if url and password and project:
        parsed = urlsplit(url)
        if parsed.scheme in ("mysql", "dolt") and parsed.hostname and parsed.port and parsed.username:
            existing_db = json.loads(target_file.read_text()).get("database") if target_file.is_file() else ""
            target = {"host": parsed.hostname, "port": parsed.port, "user": parsed.username,
                      "database": existing_db or database_name(env)}
            if not (beads / "metadata.json").is_file():
                run_env = dict(env, DOLT_CLI_PASSWORD=password, HOME=str(beads))
                quiet = {"cwd": beads, "check": False, "timeout": 30,
                         "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
                subprocess.run(["dolt", "--host", target["host"], "--port", str(target["port"]),
                                "--user", target["user"], "--no-tls", "sql", "-q",
                                f"CREATE DATABASE IF NOT EXISTS `{target['database']}`;"], env=run_env, **quiet)
                run_env.update(BD_DEFAULTS, BEADS_DIR=str(beads), BEADS_DOLT_PASSWORD=password)
                subprocess.run(["bd", "--sandbox", "--dolt-auto-commit", "off", "init",
                                "--database", target["database"], "--prefix", issue_prefix(env),
                                "--server-host", target["host"], "--server-port", str(target["port"]),
                                "--server-user", target["user"], "--skip-hooks", "--skip-agents"],
                               env=run_env, **quiet)
            write_private(target_file, json.dumps(target))
    if password:
        # Agents without the DOLT_DB_PASSWORD secret read it from here (owner-only).
        write_private(beads / ".env", f"BEADS_DOLT_PASSWORD={password}\n")
    if not target_file.is_file():
        return
    target = json.loads(target_file.read_text())
    _ensure_role(env, beads, target)
    for key, field in (("BEADS_DOLT_SERVER_HOST", "host"), ("BEADS_DOLT_SERVER_PORT", "port"),
                       ("BEADS_DOLT_SERVER_USER", "user"), ("BEADS_DOLT_SERVER_DATABASE", "database")):
        setdefault(env, key, str(target.get(field, "")))
    for key, value in BD_DEFAULTS.items():
        setdefault(env, key, value)
    env_file = beads / ".env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            if line.startswith("BEADS_DOLT_PASSWORD="):
                setdefault(env, "BEADS_DOLT_PASSWORD", line.split("=", 1)[1].strip())


CONTEXT_START = "<!-- asf:start-context -->"
CONTEXT_END = "<!-- asf:end-context -->"
TOOLS_DIR = Path("/etc/scion-tools")
# How this deployment works, for every role: Scion runs the agent in a terminal
# nobody watches. Role behaviour stays in templates/skills.
TERMINAL_RULES = """\
## Hard rule: your terminal is never read

No human or agent reads anything you write in this session, including your final
answer and the end-of-turn reply. The only channels anyone sees are `scion message`
(humans and agents) and `bd` comments (work records).

- Write no prose here: no plans, progress notes, summaries or final answers.
- Every turn that produces a result, a question or a status for someone ends with
  `scion message`, then `sciontool status ask_user|blocked|task_completed`, then
  stop without a text reply.
- Report to: {report_to}. Use it whenever no other recipient applies (no conversation
  `conv:<id>` to reply to, no completion recipient in a brief); it is valid for your
  first task too.
"""
MEMORY_RULES = """\
Use `bd remember` ONLY when you hit a real tooling, build, or environment blocker
that failed and you found the workaround, so the next agent does not repeat the
same broken attempt:
`bd remember "<1-line: do X instead of Y>" --key <blocker-topic>` (reuse `--key` to overwrite).
Never store codebase facts, architecture notes, or general conventions in `bd remember`."""


def _run(argv, env, cwd=None):
    try:
        result = subprocess.run(argv, env=env, cwd=cwd, timeout=20, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def report_to(env):
    """Default recipient: the user (@email) or agent (agent:<name>) that created this agent."""
    creator = env.get("SCION_CREATOR", "").strip()
    if "@" in creator:
        return f"@{creator}"
    return f"agent:{creator}" if creator else "the human contact in your brief"


def start_context(env, memories=None):
    """Markdown for this start: terminal rule, available build tools and the project's bd memories."""
    parts = [TERMINAL_RULES.format(report_to=f"`{report_to(env)}`"), "## Build tools in this image", ""]
    notes = sorted(TOOLS_DIR.glob("*.md")) if TOOLS_DIR.is_dir() else []
    parts += [n.read_text().strip() + "\n" for n in notes] or ["(no tool notes found)\n"]
    if env.get("DOCKER_HOST"):
        parts.append(f"Testcontainers / Docker: `{env['DOCKER_HOST']}` (rootless host socket).\n")
    parts += ["## Project memories (`bd memories`)", ""]
    if memories is None and env.get("BEADS_DIR"):
        memories = _run(["bd", "memories"], env)
    if memories is None:
        parts.append("Not loaded at start; run `bd memories` yourself.\n")
    else:
        parts.append((memories[:2000] or "No memories recorded yet.") + "\n")
    parts.append(MEMORY_RULES)
    return "\n".join(parts)


def write_start_context(env):
    """Put the managed start-context block at the top of OpenCode's global AGENTS.md (read every session).

    At the top, before the role instructions, so the terminal rule is read first.
    """
    memories = _run(["bd", "memories"], env) if env.get("BEADS_DIR") else None
    path = Path(env.get("HOME", "/home/scion")) / ".config" / "opencode" / "AGENTS.md"
    try:
        text = path.read_text() if path.is_file() else ""
        if CONTEXT_START in text and CONTEXT_END in text:
            head, rest = text.split(CONTEXT_START, 1)
            text = (head + rest.split(CONTEXT_END, 1)[1]).strip("\n")
        block = f"{CONTEXT_START}\n{start_context(env, memories=memories)}\n{CONTEXT_END}"
        text = block + "\n\n" + text + "\n" if text else block + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    except OSError as error:
        print(f"asf-entrypoint: start context not written: {error}", file=sys.stderr)


def _ensure_role(env, beads, target):
    """Set beads.role in the agent's global git config.

    bd reads beads.role from git config; without it every bd command prints
    "Run 'bd init'" and agents do. The repository's .git/config cannot hold it
    (Scion refuses unknown keys there), the agent's ~/.gitconfig can.
    """
    if _run(["git", "config", "--global", "--get", "beads.role"], env):
        return
    subprocess.run(["git", "config", "--global", "beads.role", "maintainer"], env=env, check=False,
                   timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def preserve_git_author(env):
    """Restore the human user's git author/committer identity when sciontool init set 'Scion Agent (...)'."""
    author_name = env.get("ASF_GIT_AUTHOR_NAME") or env.get("GIT_AUTHOR_NAME") or ""
    author_email = env.get("ASF_GIT_AUTHOR_EMAIL") or env.get("GIT_AUTHOR_EMAIL") or ""
    if not author_email and "@" in env.get("SCION_CREATOR", ""):
        author_email = env["SCION_CREATOR"].strip()
    if author_name:
        setdefault(env, "GIT_AUTHOR_NAME", author_name)
        setdefault(env, "GIT_COMMITTER_NAME", author_name)
        subprocess.run(["git", "config", "--global", "user.name", author_name], env=env, check=False,
                       timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if author_email:
        setdefault(env, "GIT_AUTHOR_EMAIL", author_email)
        setdefault(env, "GIT_COMMITTER_EMAIL", author_email)
        subprocess.run(["git", "config", "--global", "user.email", author_email], env=env, check=False,
                       timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


TESTCONTAINERS_SOCKET = Path("/run/testcontainers/docker.sock")


def setup_testcontainers(env, sock=TESTCONTAINERS_SOCKET):
    """Export DOCKER_HOST and Testcontainers settings when the rootless socket is mounted."""
    if not sock.is_socket():
        return
    setdefault(env, "DOCKER_HOST", f"unix://{sock}")
    setdefault(env, "TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", str(sock))
    setdefault(env, "TESTCONTAINERS_HOST_OVERRIDE", "127.0.0.1")


def prepare(env):
    scratch = shared_dir(env, "scratchpad")
    if scratch:
        try:
            (scratch / "beads").mkdir(parents=True, exist_ok=True)
        except OSError as error:
            print(f"asf-entrypoint: scratchpad not writable: {error}", file=sys.stderr)
        setdefault(env, "BEADS_DIR", str(scratch / "beads"))
    # Projects created before build-cache existed fall back to the scratchpad.
    cache = shared_dir(env, "build-cache") or (scratch / "cache" if scratch else None)
    if cache:
        setup_build_cache(env, cache)
    setup_testcontainers(env)
    preserve_git_author(env)
    setdefault(env, "BEADS_ACTOR", env.get("SCION_AGENT_NAME") or env.get("SCION_AGENT_ID") or "scion")
    setdefault(env, "BEADS_ISSUE_ID",
               bead_id_from_agent_name(env.get("SCION_AGENT_NAME", ""), re.escape(issue_prefix(env))))

    beads = Path(env["BEADS_DIR"]) if env.get("BEADS_DIR") else None
    if beads and beads.is_dir():
        try:
            init_beads(env, beads)
        except Exception as error:  # never block the agent on Beads setup
            print(f"asf-entrypoint: beads setup skipped: {error.__class__.__name__}", file=sys.stderr)
        trace_file = beads / "trace-id"
        if env.get("ASF_TRACE_ROOT", "").lower() == "true" and env.get("SCION_AGENT_ID"):
            setdefault(env, "TRACE_ID", env["SCION_AGENT_ID"])
            trace_file.write_text(env["TRACE_ID"] + "\n")
        elif trace_file.is_file():
            setdefault(env, "TRACE_ID", trace_file.read_text().strip())
    setdefault(env, "TRACE_ID", env.get("SCION_AGENT_ID") or str(uuid.uuid4()))

    return env


def main(argv):
    if not argv:
        print("usage: asf-entrypoint <command> [args...]", file=sys.stderr)
        return 2
    env = prepare(dict(os.environ))
    env.setdefault("ASF_REPORT_TO", report_to(env))
    write_start_context(env)
    os.execvpe(argv[0], argv, env)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
