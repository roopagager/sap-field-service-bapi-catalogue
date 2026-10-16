"""MCP server exposing the field-service tools to an AI assistant.

    python -m bapi_demo.server      (stdio; demo data)

Two read tools are free to call. The one write tool creates a notification only after a
dedupe check, inside a single RFC session with commit/rollback. Work orders and final
confirmations (FS-01, FS-03) are deliberately not exposed: they need human approval.
"""
from __future__ import annotations

from dataclasses import asdict

from mcp.server import MCPServer

from .fake_sap import FakeConnection, FakeSAP
from .field_service import BapiError, FieldServiceClient

mcp = MCPServer("SAP Field Service")
_sap = FakeSAP()


def _client() -> FieldServiceClient:
    # One connection per call keeps each LUW in its own session. In production: pyrfc.Connection(...)
    return FieldServiceClient(FakeConnection(_sap))


def _safe(fn):
    try:
        return fn()
    except (ValueError, BapiError) as e:
        return {"error": str(e)}


@mcp.tool()
def get_equipment(equipment: str) -> dict:
    """Read SAP equipment master data (BAPI_EQUI_GETDETAIL). Read-only."""
    return _safe(lambda: _client().get_equipment(equipment))


@mcp.tool()
def check_parts(material: str, plant: str, quantity: float, required_date: str) -> dict:
    """Check material availability at a plant (BAPI_MATERIAL_AVAILABILITY). Read-only, advisory.
    required_date is YYYYMMDD."""
    return _safe(lambda: _client().check_parts(material, plant, quantity, required_date))


@mcp.tool()
def create_service_notification(equipment: str, short_text: str, priority: str = "2") -> dict:
    """Create an SAP service notification for a reported fault (BAPI_ALM_NOTIF_CREATE + SAVE + COMMIT).
    Refuses if an open notification already exists for the equipment."""
    return _safe(lambda: asdict(_client().create_notification(equipment, short_text, priority)))


if __name__ == "__main__":
    mcp.run()
