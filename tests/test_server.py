import json

import pytest
from mcp import Client

from bapi_demo import server
from bapi_demo.fake_sap import FakeSAP


def body(r):
    return r.structured_content or json.loads(r.content[0].text)


@pytest.fixture(autouse=True)
def fresh_sap(monkeypatch):
    monkeypatch.setattr(server, "_sap", FakeSAP())


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_only_safe_tools_exposed():
    async with Client(server.mcp) as c:
        names = {t.name for t in (await c.list_tools()).tools}
    assert names == {"get_equipment", "check_parts", "create_service_notification"}


@pytest.mark.anyio
async def test_agent_flow_notification_then_duplicate():
    async with Client(server.mcp) as c:
        eq = body(await c.call_tool("get_equipment", {"equipment": "10000002"}))
        first = body(await c.call_tool("create_service_notification",
                                       {"equipment": "10000002", "short_text": "Lift stopping between floors"}))
        again = body(await c.call_tool("create_service_notification",
                                       {"equipment": "10000002", "short_text": "Lift stopping again"}))
    assert eq["manufacturer"] == "Otis"
    assert first["status"] == "CREATED" and again["status"] == "DUPLICATE"


@pytest.mark.anyio
async def test_tool_errors_are_data_not_crashes():
    async with Client(server.mcp) as c:
        r = body(await c.call_tool("check_parts", {"material": "X' or 1", "plant": "1010",
                                                   "quantity": 1, "required_date": "20261020"}))
    assert "error" in r
