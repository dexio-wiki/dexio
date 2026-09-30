"""Dexio: see what your agents know."""

# The version the server reports (MCP serverInfo, the OpenAPI document). MCP requires
# one, but no client acts on it, so it is fixed and never bumped (Forrest, 2026-09-27;
# it counted releases from 1.0.0 before that). Which commit is live is the bundle SSM
# /dexio/server-source names, not this.
__version__ = VERSION = "1.0.0"

from .parse import Graph, Page, build, scan  # noqa: F401
