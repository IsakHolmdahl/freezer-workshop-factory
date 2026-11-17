#!/usr/bin/env python3
"""Check that the AI Software Factory is installed and running, and say how to fix what is not.

Covers prerequisites, configuration files, MLflow (+ the model provider key and the
small/medium/large gateway endpoints), the Dolt database used by Beads,
and Scion (CLI, Hub, Runtime Broker, matching versions, harness images).

    python3 scripts/doctor.py            # all checks, including one tiny model call per endpoint
    python3 scripts/doctor.py --quick    # skip the model calls
"""
import argparse
from typing import Callable
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import requests

import factory_env as fe
import scion_settings
from deployments.base import DeployError
from deployments.stack import MIN_COMPOSE_VERSION, check_version, core_stack
from steps import scion_cli

OK, WARN, FAIL, SKIP = "ok", "warn", "FAIL", "skip"


@dataclass
class Result:
    status: str
    title: str
    detail: str = ""
    fix: list = field(default_factory=list)
    # Safe, automatic repair run by `doctor.py --fix` (returns a short note).
    autofix: Callable[[], str] | None = None


def _verify_scion():
    spec = importlib.util.spec_from_file_location("verify_scion", fe.SCRIPTS / "verify-scion.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = _verify_scion()


# --- prerequisites ---------------------------------------------------------

def check_tools():
    results = []
    hints = {
        "docker": "Install Docker: https://docs.docker.com/engine/install/ (macOS: Docker Desktop)",
        "git": "Install git (Ubuntu: sudo apt install git, macOS: xcode-select --install)",
        "make": "Install make (Ubuntu: sudo apt install make, macOS: xcode-select --install)",
        "go": "Install Go >= 1.26: https://go.dev/doc/install",
        "npm": "Install Node.js 22 + npm: https://nodejs.org/en/download",
    }
    for tool, hint in hints.items():
        path = fe.which(tool)
        results.append(Result(OK if path else FAIL, f"{tool} installed", path or "not found",
                              [] if path else [hint]))
    return results


def check_docker():
    info = fe.run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=20)
    if info.returncode != 0:
        if "permission denied" in info.stderr.lower():
            return [Result(FAIL, "Docker daemon reachable", "permission denied on the Docker socket",
                           ["sudo usermod -aG docker $USER", "then log out and back in (or run: newgrp docker)"])]
        return [Result(FAIL, "Docker daemon reachable", info.stderr.strip()[:200],
                       ["Start Docker: sudo systemctl start docker (macOS: open Docker Desktop)"])]
    results = [Result(OK, "Docker daemon reachable", f"server {info.stdout.strip()}")]
    results.append(check_compose_version())
    network = fe.run(["docker", "network", "inspect", fe.COMPOSE_NETWORK])
    results.append(Result(OK if network.returncode == 0 else FAIL, f"Docker network {fe.COMPOSE_NETWORK}",
                          "" if network.returncode == 0 else "missing",
                          [] if network.returncode == 0 else [f"docker network create {fe.COMPOSE_NETWORK}"],
                          autofix=None if network.returncode == 0 else
                          lambda: _ran(["docker", "network", "create", fe.COMPOSE_NETWORK])))
    return results


def check_compose_version():
    """Docker Compose v2 at MIN_COMPOSE_VERSION or newer (the compose files use features from it)."""
    title = "docker compose >= " + ".".join(map(str, MIN_COMPOSE_VERSION))
    version = core_stack(fe.config()).version()
    try:
        check_version(version)
    except DeployError as error:
        return Result(FAIL, title, error.message, list(error.fix))
    return Result(OK, title, ".".join(map(str, version)))


def check_submodule():
    ok = (fe.SCION_SRC / "Makefile").is_file()
    return [Result(OK if ok else FAIL, "scion source (git submodule)", str(fe.SCION_SRC),
                   [] if ok else ["git submodule update --init scion"])]


# --- configuration ---------------------------------------------------------

def check_config(values):
    results = []
    try:
        cfg = fe.config()
        results.append(Result(OK, "asf.yaml / asf.local.yaml readable"))
    except Exception as error:  # OSError, yaml.YAMLError, ValueError
        return [Result(FAIL, "asf.yaml / asf.local.yaml readable", str(error)[:200], ["fix the YAML"])]
    for path, fix in ((fe.SECRETS_FILE, f"cp {fe.SECRETS_TEMPLATE.name} {fe.SECRETS_FILE.name}"),
                      (fe.ENV_FILE, "./setup.py (writes .env from asf.yaml)")):
        ok = path.is_file()
        results.append(Result(OK if ok else FAIL, f"{path.name} exists", "", [] if ok else [fix]))
    stale = {k: v for k, v in fe.compose_env_values(cfg).items() if values.get(k) != v}
    results.append(Result(OK if not stale else WARN, ".env matches asf.yaml", ", ".join(stale),
                          [] if not stale else ["./setup.py (rewrites .env), then: docker compose up -d"],
                          autofix=None if not stale else
                          lambda: "rewrote " + ", ".join(fe.write_compose_env()) + " (then: docker compose up -d)"))
    provider = fe.model_provider(cfg)
    key_ok = not fe.provider_needs_key(cfg) or not fe.is_placeholder(values.get("MODEL_PROVIDER_API_KEY"))
    if fe.gateway_type(cfg) == "mlflow":
        results.append(Result(OK if key_ok else FAIL, f"MODEL_PROVIDER_API_KEY set for provider {provider}",
                              "" if key_ok else "missing in .env.secrets",
                              [] if key_ok else ["make setup (asks for it), or add it to .env.secrets, then: "
                                                 "docker compose up -d --force-recreate mlflow"]))
    try:
        results.append(Result(OK, "agent gateway configured", f"{fe.gateway_url(cfg)} ({fe.gateway_type(cfg)})"))
    except ValueError as error:
        results.append(Result(FAIL, "agent gateway configured", str(error), ["set agent_gateway.url in asf.local.yaml"]))
    volume = core_stack(cfg).volume("dolt-data")
    for key in ("DOLT_DB_PASSWORD", "DOLT_ROOT_PASSWORD"):
        if fe.DOLT_PASSWORD_RE.match(values.get(key, "")):
            results.append(Result(OK, f"{key} set in .env.secrets"))
        elif volume:
            # The database was created with the compose default and keeps it; nothing to fix
            # without recreating it. Dolt only listens on 127.0.0.1.
            results.append(Result(OK, f"{key}", f"compose default (database {volume} was created with it; "
                                  "only reachable on 127.0.0.1). New password: ./cleanup.py (stack), then ./setup.py"))
        else:
            results.append(Result(WARN, f"{key} set in .env.secrets", "missing; ./setup.py generates it",
                                  ["./setup.py (before the first Dolt start)"]))
    email = values.get("SCION_ADMIN_EMAIL")
    results.append(Result(OK if not fe.is_placeholder(email) else WARN, "SCION_ADMIN_EMAIL set",
                          email or "", [] if not fe.is_placeholder(email) else
                          ["./setup.py asks for it, or edit .env.secrets"]))
    return results


# --- infrastructure --------------------------------------------------------

def compose_services(values):
    return {row["Service"]: row for row in core_stack(fe.config()).ps(all=True) if "Service" in row}


def _compose_hint(*args):
    """Shell command, as the user would type it, for the core compose stack."""
    return shlex.join(core_stack(fe.config()).argv(*args))


def check_services(values):
    services = compose_services(values)
    results = []
    for name in fe.CORE_SERVICES:
        item = services.get(name)
        if not item:
            results.append(Result(FAIL, f"service {name} running", "not created",
                                  [_compose_hint("up", "-d", name)]))
            continue
        state, health = item.get("State"), item.get("Health") or "no healthcheck"
        ok = state == "running" and health in ("healthy", "no healthcheck")
        fix = [] if ok else [_compose_hint("logs", "--tail", "50", name), _compose_hint("up", "-d", name)]
        if name == "dolt" and not ok:
            fix.append("Dolt keeps its data in the dolt-data volume: never run `docker compose down -v`. "
                       "See dolt/README.md")
        results.append(Result(OK if ok else FAIL, f"service {name} running", f"{state}, {health}", fix))
    return results, services


def check_gateway(values, quick):
    """Model calls through the agent gateway; MLflow-specific checks for type mlflow."""
    results = check_mlflow() if fe.gateway_type() == "mlflow" else []
    if any(r.status == FAIL for r in results):
        return results
    for name in fe.gateway_models():
        results.append(Result(OK, f"model {name}", "call skipped (--quick)") if quick else check_inference(name))
    return results


def check_mlflow():
    url = fe.mlflow_url()
    try:
        requests.get(f"{url}/health", timeout=5).raise_for_status()
    except requests.RequestException as error:
        return [Result(FAIL, "MLflow reachable", f"{url}: {error.__class__.__name__}",
                       ["docker compose logs --tail 50 mlflow", "docker compose up -d mlflow"])]
    results = [Result(OK, "MLflow reachable", url)]

    seed = "docker exec mlflow-server python /opt/mlflow-bootstrap/seed_gateway.py"
    try:
        listed = requests.get(f"{url}/api/3.0/mlflow/gateway/endpoints/list", timeout=10).json()
        names = {e["name"] for e in listed.get("endpoints", [])}
    except (requests.RequestException, ValueError) as error:
        return results + [Result(FAIL, "MLflow gateway endpoints", str(error), [seed])]
    missing = [n for n in fe.gateway_models() if n not in names]
    results.append(Result(OK if not missing else FAIL, "MLflow gateway endpoints",
                          ", ".join(missing) and f"missing: {', '.join(missing)}", [] if not missing else [seed]))
    return results


def check_inference(name):
    """One tiny chat completion through AGENT_GATEWAY_URL, as an agent would send it."""
    url = fe.gateway_url()
    key = fe.settings().get("AGENT_GATEWAY_API_KEY")
    headers = {"Authorization": f"Bearer {key}"} if not fe.is_placeholder(key) else {}
    try:
        response = requests.post(f"{url}/chat/completions", timeout=120, headers=headers, json={
            "model": name, "max_tokens": 5,
            "messages": [{"role": "user", "content": "Reply with: ok"}]})
        response.raise_for_status()
        model = response.json().get("model", "?")
        return Result(OK, f"model {name}", f"answers ({model})")
    except (requests.RequestException, ValueError) as error:
        body = getattr(getattr(error, "response", None), "text", "")[:200]
        fix = ([f"check MODEL_PROVIDER_API_KEY in .env.secrets and that provider {fe.model_provider()} serves the model",
                f"change it with mlflow.endpoints.{name} (or mlflow.provider) in asf.local.yaml, then ./setup.py"]
               if fe.gateway_type() == "mlflow" else
               [f"check that {url} serves model '{name}' and AGENT_GATEWAY_API_KEY in .env.secrets"])
        return Result(FAIL, f"model {name}", f"model call failed: {error} {body}".strip(), fix)


def check_dolt(values):
    health = fe.run(core_stack(fe.config()).argv("exec", "-T", "dolt", "python3", "/opt/dolt/dolt.py", "health"), timeout=30)
    ok = health.returncode == 0
    return [Result(OK if ok else FAIL, "Dolt (Beads database) answers", (health.stdout + health.stderr).strip()[:200],
                   [] if ok else ["docker compose logs --tail 50 dolt", "see dolt/README.md (Setup)"])]


# --- scion -----------------------------------------------------------------

def scion_server_processes():
    """(pid, user, args) of running `scion server start` processes, without the sciontool wrapper."""
    out = fe.run(["ps", "-eo", "pid=,user=,args="]).stdout
    found = []
    for line in out.splitlines():
        pid, _, rest = line.strip().partition(" ")
        user, _, args = rest.strip().partition(" ")
        words = args.split()
        if len(words) >= 3 and Path(words[0]).name == "scion" and words[1:3] == ["server", "start"]:
            found.append((pid, user, args))
    return found


def check_scion_home_owner():
    """A Hub that ran as root leaves ~/.scion unreadable and unwritable for the scion CLI."""
    home = fe.scion_home()
    default = Path.home() / ".scion"
    if home.resolve() != default.resolve():
        return [Result(FAIL, "Scion home matches the CLI global directory",
                       f"configured {home}, but {default} does not point there",
                       ["make setup (creates the compatibility symlink if ~/.scion is absent)",
                        "stop Scion and migrate existing ~/.scion data before changing locations"])]
    if not home.exists():
        return []
    foreign = [p for p in (home / "hub.db", home / "dev-token") if p.exists() and p.stat().st_uid != os.getuid()]
    if not foreign:
        return [Result(OK, "~/.scion is owned by you", str(home))]

    def autofix():
        # A throwaway root container does the chown, so no sudo prompt is needed.
        owner = f"{os.getuid()}:{os.getgid()}"
        chown = fe.run(["docker", "run", "--rm", "-v", f"{home}:/target", "busybox", "chown", "-R", owner, "/target"])
        if chown.returncode != 0:
            return f"chown failed: {(chown.stderr or chown.stdout).strip()[:200]}"
        return f"{home} now owned by {owner}"

    return [Result(FAIL, "~/.scion is owned by you", ", ".join(p.name for p in foreign) + " owned by another user",
                   [f"sudo chown -R {os.getuid()}:{os.getgid()} {home}", "scion server restart"], autofix)]


def check_dev_token():
    """The dev token the scion CLI sends must be accepted by the Hub (a stale ~/.scion/dev-token gives 401)."""
    token, source = os.environ.get("SCION_DEV_TOKEN", "").strip(), "SCION_DEV_TOKEN"
    if not token:
        home = fe.scion_home()
        path = Path(os.environ.get("SCION_DEV_TOKEN_FILE") or home / "dev-token")
        source = str(path)
        token = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    title = "Scion dev token is accepted by the Hub"
    if not token:
        return [Result(WARN, title, f"no dev token ({source})",
                       ["scion server restart (writes ~/.scion/dev-token)"])]
    try:
        response = requests.get(f"{fe.hub_url()}/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=5)
    except requests.RequestException as exc:
        return [Result(SKIP, title, f"Hub not reachable: {exc.__class__.__name__}")]
    if response.status_code == 200:
        return [Result(OK, title, source)]
    if response.status_code not in (401, 403):
        return [Result(WARN, title, f"{source}: Hub answered HTTP {response.status_code}")]

    fix = ["unset SCION_DEV_TOKEN (it overrides the token file)"] if source == "SCION_DEV_TOKEN" else [
        f"rm {source}", "scion server restart"]
    return [Result(FAIL, title, f"{source}: Hub rejected it (HTTP {response.status_code}); the token is stale", fix)]


def check_scion_instances():
    """One Hub and at most one Runtime Broker should run on this machine."""
    hubs, brokers = [], []
    for pid, user, args in scion_server_processes():
        flags = args.split()
        hosted = "--hosted" in flags or "--production" in flags
        if "--enable-hub" in flags or not hosted:
            hubs.append((pid, user, args))
        if "--enable-hub" not in flags and "--enable-runtime-broker" in flags:
            brokers.append((pid, user, args))
    results = []
    if len(hubs) > 1:
        detail = "; ".join(f"pid {pid} ({user})" for pid, user, _ in hubs)
        results.append(Result(FAIL, "only one Scion Hub process runs", detail,
                              ["scion server stop", "kill <pid> for a stray process"]))
    if len(brokers) > 1:
        detail = "; ".join(f"pid {pid} ({user})" for pid, user, _ in brokers)
        results.append(Result(WARN, "only one Scion Runtime Broker process runs", detail,
                              ["scion runtime-broker stop", "kill <pid> for a stray process"]))
    return results


def check_scion_binaries():
    """Check host CLI locations even when Docker or the Hub is unavailable."""
    binaries = scion_cli.discover()
    choose = "./setup.py --only scion_cli"
    if not binaries:
        return [Result(FAIL, "Scion CLI installation", "no executable found on PATH or in common locations",
                       ["./setup.py --only scion_cli,build_scion"])]
    recommended = binaries[0]
    results = [Result(OK, "Scion CLI recommended location", str(recommended))]
    # Symlink aliases are the same installation, as in setup's duplicate cleanup.
    unique = {path.resolve() for path in binaries}
    if len(unique) > 1:
        results.append(Result(WARN, "duplicate Scion CLI installations",
                              "; ".join(map(str, binaries)),
                              [f"{choose} (choose a location and optionally remove duplicates)"]))
    configured = fe.install_path()
    if configured.resolve() != recommended.resolve():
        results.append(Result(WARN, "Scion CLI install location matches recommendation",
                              f"configured {configured}; recommended {recommended}", [choose]))
    active = fe.which("scion")
    if not active:
        results.append(Result(FAIL, "Scion CLI on PATH", f"installed at {recommended}, but not on PATH",
                              [f'export PATH={shlex.quote(str(recommended.parent))}:"$PATH"',
                               "add that line to your shell profile", choose]))
    elif Path(active).resolve() != configured.resolve():
        results.append(Result(WARN, "Scion CLI on PATH matches configured installation",
                              f"PATH {active}; configured {configured}", [choose]))
    else:
        results.append(Result(OK, "Scion CLI on PATH matches configured installation", active))
    return results


def check_scion(values, services):
    results = []
    binary = fe.which("scion")
    if not binary:
        return [Result(FAIL, "scion CLI on PATH", "not found",
                       ["python3 scripts/sync-factory.py --skip-skills --skip-templates",
                        'and make sure ~/.local/bin is on PATH: export PATH="$HOME/.local/bin:$PATH"'])]
    version, commit = VERIFY.cli_version(binary)
    results.append(Result(OK, "scion CLI on PATH", f"{binary} ({version}, commit {commit})"))
    built = fe.SCION_SRC / "build" / "scion"
    if built.is_file():
        same = VERIFY.sha256(VERIFY.upstream_binary(binary)) == VERIFY.sha256(built)
        results.append(Result(OK if same else WARN, "scion CLI is the one built from ./scion", "",
                              [] if same else ["./setup.py --only build_scion"]))
    submodule = fe.scion_commit()
    if commit and submodule != "unknown":
        same = submodule.startswith(commit)
        results.append(Result(OK if same else WARN, "scion CLI matches the scion submodule commit",
                              f"CLI {commit}, submodule {submodule[:8]}",
                              [] if same else ["python3 scripts/sync-factory.py --skip-skills --skip-templates"]))

    status = fe.run(["scion", "server", "status", "--json"])
    try:
        local_running = json.loads(status.stdout).get("daemonRunning", False)
    except ValueError:
        local_running = False
    results.extend(check_scion_instances())
    results.extend(check_scion_home_owner())
    results.extend(check_dev_token())
    results.append(Result(OK if local_running else FAIL, "Scion server running (Hub, broker, web UI)", "",
                          [] if local_running else ["scion server start"]))

    try:
        broker = VERIFY.healthz(fe.broker_url())
        match = VERIFY.versions_match(broker.get("version"), version, commit)
        results.append(Result(OK, "Runtime Broker answers", f"{fe.broker_url()} ({broker.get('status')})"))
        results.append(Result(OK if match else FAIL, "Runtime Broker version matches CLI",
                              f"broker {broker.get('version')}, CLI {version}/{commit}",
                              [] if match else ["scion server restart"]))
    except (requests.RequestException, ValueError) as error:
        results.append(Result(FAIL, "Runtime Broker answers", f"{fe.broker_url()}: {error.__class__.__name__}",
                              ["start Scion (see 'Scion server running' above)"]))
    try:
        hub = VERIFY.healthz(fe.hub_url())
        results.append(Result(OK, "Hub answers", f"{fe.hub_url()} ({hub.get('status')})"))
    except (requests.RequestException, ValueError) as error:
        results.append(Result(FAIL, "Hub answers", f"{fe.hub_url()}: {error.__class__.__name__}",
                              ["start Scion (see 'Scion server running' above)"]))
        return results

    current = fe.run(["scion", "hub", "list"])
    selected = current.returncode == 0 and "No hubs registered" not in current.stdout
    results.append(Result(OK if selected else FAIL, "CLI has a hub selected", "",
                          [] if selected else [f"scion hub add local {fe.hub_url()} --use"],
                          autofix=None if selected else
                          lambda: _ran(["scion", "--non-interactive", "hub", "add", "local", fe.hub_url(), "--use"])))
    fix = ["./setup.py  (tick: Publish roles, skills and Hub settings)"]
    for key, expected in (("AGENT_GATEWAY_URL", fe.gateway_url()), ("AGENT_GATEWAY_TYPE", fe.gateway_type()),
                          ("AGENT_GATEWAY_API_KEY", "")):
        out = fe.run(["scion", "--non-interactive", "hub", "env", "get", "--scope", "hub", key]).stdout
        ok = key in out and expected in out and "as-needed" not in out
        results.append(Result(OK if ok else FAIL, f"Agents get {key} (always)", expected, [] if ok else fix))
    dolt_url = fe.run(["scion", "--non-interactive", "hub", "env", "get", "--scope", "hub", "DOLT_URL"])
    ok = dolt_url.returncode == 0 and "mysql://" in dolt_url.stdout
    results.append(Result(OK if ok else FAIL, "Hub knows the Beads database (DOLT_URL)",
                          "" if ok else (dolt_url.stderr or dolt_url.stdout).strip()[:200],
                          [] if ok else ["./setup.py  (tick: Publish roles, skills and Hub settings)"]))
    return results


def check_git_config():
    """Scion refuses agent worktrees when a checkout's .git/config has keys outside its allow-list."""
    import git_config_check as gcc
    repos = list(dict.fromkeys([fe.ROOT, *gcc.linked_checkouts(fe.run)]))
    results = []
    for repo in repos:
        keys = gcc.refused_keys(repo)
        fix = [f"git -C {repo} config --unset-all {key}" for key in keys]
        if any(gcc.is_tool_metadata(k) for k in keys):
            fix.append("(editor/gh metadata: `doctor.py --fix` removes it)")
        metadata = [k for k in keys if gcc.is_tool_metadata(k)]
        autofix = None
        if metadata:
            def autofix(repo=repo, metadata=metadata):
                return "removed " + ", ".join(k for k in metadata if gcc.remove(repo, k))
        results.append(Result(FAIL if keys else OK, f"git config allows agent worktrees: {repo.name}",
                              ", ".join(keys), fix, autofix))
    return results


def _broker_realigner():
    spec = importlib.util.spec_from_file_location("realign_brokers", fe.SCRIPTS / "realign-brokers.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _realign_project(module, project_id, broker_id):
    # Re-query and re-plan at repair time: assignments may have changed since the check.
    if module.main(["--apply", "--project", project_id, "--broker", broker_id]):
        raise RuntimeError("broker realignment failed; see project errors above")
    return "realigned project to the embedded broker"


def check_broker_alignment():
    """Use the realignment planner without writes; --fix re-plans each affected project."""
    module = _broker_realigner()
    errors = (ValueError, OSError, subprocess.SubprocessError)
    title = "Projects aligned to embedded broker"
    fix = ["python3 scripts/realign-brokers.py", "python3 scripts/realign-brokers.py --apply"]
    try:
        brokers = [b for b in module.query("hub", "brokers")
                   if b.get("labels", {}).get("scion.io/broker-role") == "embedded"]
        if not brokers:
            return [Result(SKIP, title, "no embedded broker; external broker assignments are kept")]
        if len(brokers) != 1:
            raise ValueError("multiple embedded brokers; select one with realign-brokers.py --broker")
        target = module.select_broker(brokers, brokers[0]["id"])
        projects = module.query("hub", "projects")
    except errors as error:
        return [Result(FAIL, title, str(error), fix)]
    results = []
    for row in projects:
        if row["slug"] == "global":
            continue
        project_id = row["id"]
        project_title = f"Broker alignment: {row['slug']}"
        command = shlex.join(["python3", "scripts/realign-brokers.py", "--project", project_id,
                              "--broker", target["id"]])
        try:
            project = module.query("hub", "project", "info", project_id)
            agents = module.query("list", "--project", project_id)
            commands, kept = module.plan(project, target, agents)
        except errors as error:
            results.append(Result(FAIL, project_title, str(error),
                                  [command + " --path " + shlex.quote(f"{project_id}=/absolute/checkout")]))
            continue
        detail = f"{len(commands)} change(s) needed" if commands else "aligned"
        if kept:
            detail += "; retaining providers with assigned agents: " + ", ".join(
                f"{broker} ({', '.join(names)})" for broker, names in kept)
        results.append(Result(
            WARN if commands else OK, project_title, detail,
            [command + " --apply"] if commands else [],
            (lambda project_id=project_id: _realign_project(module, project_id, target["id"]))
            if commands else None))
    return results or [Result(OK, title, "no projects to realign")]


def check_scion_settings(values):
    path = scion_settings.settings_path()
    if not path.is_file():
        return [Result(FAIL, f"{path} exists", "missing", ["./setup.py (creates it)"])]
    import yaml
    try:
        current = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as error:
        return [Result(FAIL, f"{path} is valid YAML", str(error)[:200],
                       [f"fix the file by hand or restore a backup: ls {path.parent}/settings.yaml.bak-*"])]
    expected = scion_settings.render(current, values)
    results = [Result(OK, f"{path} exists")]
    ok = expected == current
    results.append(Result(OK if ok else WARN, "Scion settings complete (hub endpoint, identity, shared dirs)",
                          "" if ok else "settings differ from what ./setup.py would write",
                          [] if ok else ["./setup.py (keeps your changes, makes a backup)"]))
    if fe.get("testcontainers.enabled", True):
        sock = scion_settings.rootless_docker_socket()
        ok = sock.is_socket()
        results.append(Result(OK if ok else WARN, "Rootless Docker socket for Testcontainers",
                              str(sock) if ok else f"missing {sock}",
                              [] if ok else [
                                  ".venv/bin/python scripts/testcontainers-docker.py enable",
                                  "or run ./setup.py (tick: Rootless Docker for Testcontainers)"]))
    return results


def check_images():
    results = []
    for image, fix in (("scion-local-base:latest", "python3 scripts/build-scion-base-image.py"),
                       ("asf-opencode:latest", "python3 scripts/sync-factory.py --skip-skills --skip-templates")):
        ok = fe.run(["docker", "image", "inspect", image]).returncode == 0
        results.append(Result(OK if ok else FAIL, f"image {image}", "" if ok else "missing", [] if ok else [fix]))
    bd = fe.which("bd")
    results.append(Result(OK if bd else WARN, "bd (Beads CLI) on PATH, optional for humans", bd or "not found",
                          [] if bd else ["https://github.com/steveyegge/beads#installation"]))
    return results


# --- runner ----------------------------------------------------------------

def all_checks(quick=False):
    """Yield (section, [Result]) in dependency order; later sections are skipped if Docker is down."""
    values = fe.settings()
    yield "Prerequisites", check_tools() + check_submodule()
    docker = check_docker()
    yield "Docker", docker
    yield "Configuration", check_config(values)
    yield "Scion CLI", check_scion_binaries()
    if docker[0].status != OK:
        yield "Services", [Result(SKIP, "remaining checks", "Docker is not available")]
        return
    services_results, services = check_services(values)
    yield "Services", services_results
    yield "Agent gateway", check_gateway(values, quick)
    yield "Beads database", check_dolt(values)
    yield "Scion", check_git_config() + check_scion_settings(values) + check_scion(values, services)
    yield "Broker alignment", check_broker_alignment()
    yield "Images and tools", check_images()


def _ran(argv):
    result = fe.run(argv, timeout=60)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[:200])
    return "ran: " + " ".join(argv)


def apply_fixes(sections):
    """Run the automatic repairs of failed/warned checks; returns how many succeeded."""
    done = 0
    for _, results in sections:
        for result in results:
            if result.status in (FAIL, WARN) and result.autofix:
                try:
                    print(f"  fixed  {result.title}: {result.autofix()}")
                    done += 1
                except Exception as error:  # report and continue
                    print(f"  failed {result.title}: {error}")
    return done


def print_results(sections):
    counts = {OK: 0, WARN: 0, FAIL: 0, SKIP: 0}
    failed = []
    for section, results in sections:
        print(f"\n== {section}")
        for result in results:
            counts[result.status] += 1
            detail = f": {result.detail}" if result.detail else ""
            print(f"  [{result.status:>4}] {result.title}{detail}")
            for fix in result.fix:
                print(f"         fix: {fix}")
            if result.autofix and result.status in (FAIL, WARN):
                print("         auto: ./setup.py --doctor --fix (or doctor.py --fix) repairs this")
            if result.status == FAIL:
                failed.append(result)
    print(f"\n{counts[OK]} ok, {counts[WARN]} warning(s), {counts[FAIL]} failure(s)")
    if failed:
        print("Fix the first failure, then run this again; later checks often depend on earlier ones.")
    return failed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--quick", action="store_true", help="skip the model calls to each endpoint")
    parser.add_argument("--fix", action="store_true",
                        help="apply the safe automatic repairs (marked 'auto'), then check again")
    args = parser.parse_args(argv)
    if args.fix:
        sections = list(all_checks(args.quick))
        print("== Repairs")
        if not apply_fixes(sections):
            print("  nothing to repair automatically")
    return 1 if print_results(all_checks(args.quick)) else 0


if __name__ == "__main__":
    sys.exit(main())
