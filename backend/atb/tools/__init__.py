"""Tool registry. Importing this package registers the built-in tools."""

import logging
from importlib.metadata import entry_points

from . import devops, execute, files, session, snapshots, system  # noqa: F401
from .base import REGISTRY, Context, Operation, ToolSpec, tool

log = logging.getLogger(__name__)

__all__ = ["REGISTRY", "Context", "Operation", "ToolSpec", "load_plugins", "tool"]


def load_plugins() -> list[str]:
    """Import third-party tool modules advertised in the `atb.tools` entry-point group.

    Plugins run inside the bridge process with full trust; install only code you audited.
    """
    loaded = []
    for point in entry_points(group="atb.tools"):
        try:
            point.load()
            loaded.append(point.name)
        except Exception:
            log.exception("failed to load tool plugin %s", point.name)
    return loaded
