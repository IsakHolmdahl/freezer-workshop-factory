#!/usr/bin/env python3
"""Enable, check or disable rootless Docker on the host for agent Testcontainers tests.

Agents cannot run Docker-in-Docker inside Scion containers, and mounting the
system /var/run/docker.sock would give agents root access to the host. Instead,
this script sets up a separate rootless Docker daemon under your user account
(/run/user/<uid>/docker.sock) and mounts that socket into agents at
/run/testcontainers/docker.sock via ~/.scion/settings.yaml.

    testcontainers-docker.py status
    testcontainers-docker.py enable     # default command
    testcontainers-docker.py disable
"""
import argparse
import getpass
import subprocess
import sys
import time
from pathlib import Path

import factory_env as fe
import scion_settings


class Failure(Exception):
    pass


def sh(argv, check=True, quiet=False):
    print(f"+ {' '.join(str(a) for a in argv)}", flush=True)
    result = subprocess.run([str(a) for a in argv], cwd=fe.ROOT,
                            stdout=subprocess.DEVNULL if quiet else None)
    if check and result.returncode != 0:
        raise Failure(f"command failed (exit {result.returncode}): {' '.join(str(a) for a in argv)}")
    return result.returncode == 0


def wait_for_socket(sock: Path, timeout: int = 20) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if sock.is_socket():
            return True
        time.sleep(1)
    return sock.is_socket()


def check_subids(user: str) -> bool:
    for path in (Path("/etc/subuid"), Path("/etc/subgid")):
        if not path.is_file() or not any(line.startswith(f"{user}:") for line in path.read_text().splitlines()):
            return False
    return True


def ensure_prereqs(interactive: bool = True) -> None:
    missing = [cmd for cmd in ("newuidmap", "newgidmap", "dockerd-rootless-setuptool.sh", "slirp4netns")
               if not fe.which(cmd)]
    if missing:
        print(f"Missing packages for rootless Docker ({', '.join(missing)}); installing via sudo apt-get ...",
              flush=True)
        if not interactive and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode != 0:
            raise Failure(
                "missing prerequisites; run: sudo apt-get update && "
                "sudo apt-get install -y uidmap dbus-user-session slirp4netns docker-ce-rootless-extras")
        sh(["sudo", "apt-get", "update", "-qq"])
        sh(["sudo", "apt-get", "install", "-y", "uidmap", "dbus-user-session", "slirp4netns"])
    user = getpass.getuser()
    if not check_subids(user):
        sh(["sudo", "usermod", "--add-subuids", "100000-165535", "--add-subgids", "100000-165535", user])


def enable(interactive: bool = True) -> int:
    sock = scion_settings.rootless_docker_socket()
    fe.set_local("testcontainers.enabled", True)
    if not sock.is_socket():
        ensure_prereqs(interactive=interactive)
        # --force: keep system /var/run/docker.sock untouched for compose/Scion.
        sh(["dockerd-rootless-setuptool.sh", "install", "--force"])
        sh(["systemctl", "--user", "enable", "--now", "docker.service"])
        sh(["loginctl", "enable-linger", getpass.getuser()], check=False, quiet=True)
        # Keep the host's default docker CLI context on system Docker.
        sh(["docker", "context", "use", "default"], check=False, quiet=True)
        if not wait_for_socket(sock):
            raise Failure(f"rootless Docker socket {sock} did not appear; check: "
                          "systemctl --user status docker.service")
    probe = fe.run(["docker", "-H", f"unix://{sock}", "info", "--format", "{{.ServerVersion}}"], timeout=15)
    if probe.returncode != 0:
        raise Failure(f"rootless Docker at {sock} is not responding: {(probe.stderr or probe.stdout).strip()[:200]}")
    path, changed, _ = scion_settings.ensure(fe.settings(), tc_socket=sock)
    print(f"rootless Docker {probe.stdout.strip()} ready at {sock}")
    print(f"{'updated' if changed else 'verified'} {path} "
          f"(mounted at {scion_settings.TESTCONTAINERS_TARGET_SOCKET} for new agents)")
    return 0


def disable() -> int:
    fe.set_local("testcontainers.enabled", False)
    sh(["systemctl", "--user", "disable", "--now", "docker.service"], check=False)
    sh(["docker", "context", "use", "default"], check=False, quiet=True)
    path, _, _ = scion_settings.ensure(fe.settings())
    print(f"disabled Testcontainers rootless Docker and removed mount from {path}")
    return 0


def status() -> int:
    enabled = bool(fe.get("testcontainers.enabled", True))
    sock = scion_settings.rootless_docker_socket()
    mounted = scion_settings.testcontainers_volume(sock) is not None
    print(f"config enabled:  {enabled}")
    print(f"socket:          {sock} ({'present' if sock.is_socket() else 'missing'})")
    if sock.is_socket():
        probe = fe.run(["docker", "-H", f"unix://{sock}", "info", "--format", "{{.ServerVersion}}"], timeout=10)
        version = probe.stdout.strip() if probe.returncode == 0 else "unreachable"
        print(f"daemon version:  {version}")
    print(f"agent mount:     {scion_settings.TESTCONTAINERS_TARGET_SOCKET if mounted else 'not mounted'}")
    return 0 if (enabled and sock.is_socket() and mounted) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("command", nargs="?", default="enable", choices=("enable", "status", "disable"))
    parser.add_argument("--non-interactive", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            return status()
        if args.command == "disable":
            return disable()
        return enable(interactive=not args.non_interactive)
    except Failure as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
