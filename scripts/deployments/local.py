"""Local deployment: Scion Hub and Runtime Broker run on this machine (`scion server start`)."""
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

import factory_env as fe
import scion_settings
from .base import (FAIL, OK, Adapter, Check, CommandResult, DeployError, DeploymentStatus,
                   Endpoints, Resource, is_read_only_scion)


class LocalAdapter(Adapter):
    name = "local"

    def start(self, *, build: bool = True) -> DeploymentStatus:
        status = self.status()
        if status.running:
            return status
        result = self._run(["scion", "server", "start"], mutating=True, timeout=120)
        if not result.ok:
            detail = "\n".join(text.strip() for text in (result.stdout, result.stderr) if text.strip())
            message = "`scion server start` failed" + ("\n" + detail if detail else "")
            raise DeployError(message, fix=[
                "scion server status", "scion runtime-broker status",
                "stop a standalone broker before starting the combined local server"])
        return self.status()

    def stop(self) -> None:
        self._run(["scion", "server", "stop"], mutating=True, timeout=120)

    def status(self) -> DeploymentStatus:
        result = self._run(["scion", "server", "status", "--json"], timeout=20)
        running = result.ok and '"daemonRunning": true' in result.stdout
        return DeploymentStatus(running=running, healthy=running,
                                detail="scion server running" if running else "scion server not running")

    def scion(self, args: Sequence[str], *, input: str | None = None, interactive: bool = False,
              timeout: int = 60) -> CommandResult:
        return self._run(["scion", *args], input=input, timeout=timeout,
                         mutating=not is_read_only_scion(args))

    def run_repo_script(self, script: str, args: Sequence[str]) -> CommandResult:
        return self._run([sys.executable, fe.SCRIPTS / script, *args], mutating=True, timeout=3600)

    @contextmanager
    def stage_bundle(self, path: Path) -> Iterator[Path]:
        yield Path(path)

    def settings_path(self) -> Path:
        return scion_settings.settings_path()

    def render_settings(self, current: dict) -> dict:
        return scion_settings.render(current, fe.settings())

    def service_endpoints(self) -> Endpoints:
        hub = fe.hub_url(self.cfg)
        return Endpoints(hub_human=hub, hub_agent=hub, broker=fe.broker_url(self.cfg),
                         mlflow=fe.mlflow_url(self.cfg), dolt=fe.dolt_url(self.cfg))

    def health_checks(self) -> list[Check]:
        status = self.status()
        if status.running:
            return [Check(OK, "Scion server", status.detail)]
        return [Check(FAIL, "Scion server", status.detail,
                      ["scion server start"])]

    def resources(self) -> list[Resource]:
        # Local Scion keeps its state in files, not Docker resources: nothing to inventory.
        return []
