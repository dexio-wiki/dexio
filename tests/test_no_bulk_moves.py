"""Bulk import was removed 2026-09-27 (Forrest): no route takes a whole wiki at
once, and the MCP instructions do not advertise one. Export was removed with it
and came back 2026-09-28 (Forrest); see test_export.py."""
from __future__ import annotations

import httpx
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from dexio.server import db  # noqa: E402
from dexio.server.mcp_server import INSTRUCTIONS  # noqa: E402

from test_mcp_parity import PAGES, server  # noqa: E402,F401


def test_push_route_is_gone(server):
    body = {"project": "kb2", "merge": True,
            "files": [{"path": p, "text": t} for p, t in PAGES.items()]}
    r = httpx.post(f"{server['base']}/api/v1/push", json=body,
                   headers={"Authorization": f"Bearer {server['fleet']}"})
    assert r.status_code in (404, 405), r.status_code
    assert not db.project_exists(server["conn"], db.key(db.default_workspace(server["conn"]),
                                                        "kb2"))


def test_instructions_do_not_advertise_bulk_import():
    assert "/api/v1/push" not in INSTRUCTIONS
