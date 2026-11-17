"""Deployment adapters: select the adapter for `scion.deployment` and expose its contract."""
from typing import Callable

import factory_env as fe
from .base import (Adapter, Check, CommandResult, DeployError, DeploymentStatus, Endpoints,
                   ImageStatus, Resource)
from .local import LocalAdapter
from .stack import ComposeStack, core_stack

ADAPTERS: dict[str, type[Adapter]] = {
    "local": LocalAdapter,
}


def get_adapter(cfg=None, *, dry_run: bool = False, log: Callable[[str], None] = print,
                name: str | None = None) -> Adapter:
    """The local adapter: Hub, hosted Runtime Broker and web UI on this machine."""
    cfg = cfg if cfg is not None else fe.config()
    selected = name or "local"
    if selected not in ADAPTERS:
        raise DeployError(f"unknown deployment: {selected!r}", fix=["only `local` is supported"])
    return ADAPTERS[selected](cfg, dry_run=dry_run, log=log)


__all__ = [
    "Adapter", "Check", "CommandResult", "ComposeStack", "DeployError", "DeploymentStatus",
    "Endpoints", "ImageStatus", "Resource", "core_stack", "get_adapter",
]
