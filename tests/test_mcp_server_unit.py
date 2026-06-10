from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import pytest

from marginalia.agent.tools import ToolContext, ToolRegistration
from marginalia import mcp_server


@pytest.mark.asyncio
async def test_mcp_initialize_and_lists_readonly_tools() -> None:
    initialized = await mcp_server.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert initialized is not None
    assert initialized["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSION
    assert "tools" in initialized["result"]["capabilities"]

    listed = await mcp_server.handle_message(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    )
    assert listed is not None
    tools = {tool["name"]: tool for tool in listed["result"]["tools"]}
    for name in (
        "recall_knowledge",
        "read_files",
        "search_metadata",
        "search_journal",
        "read_entries_metadata",
    ):
        assert name in tools
        assert "inputSchema" in tools[name]

    assert "generate_chart" not in tools
    assert "query_sql" not in tools


@pytest.mark.asyncio
async def test_mcp_call_invokes_registered_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ToolContext, dict[str, Any]]] = []

    async def fake_handler(db: Any, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        calls.append((db, ctx, args))
        return {"ok": True, "args": args, "conversation_id": ctx.conversation_id}

    fake_registration = ToolRegistration(
        name="search_journal",
        description="fake",
        input_schema={"type": "object"},
        handler=fake_handler,
    )

    @asynccontextmanager
    async def fake_session_scope():
        yield "db-session"

    monkeypatch.setattr(mcp_server, "get_tool", lambda name: fake_registration)
    monkeypatch.setattr(mcp_server, "session_scope", fake_session_scope)

    response = await mcp_server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": "call-1",
            "method": "tools/call",
            "params": {
                "name": "search_journal",
                "arguments": {"text": "policy", "limit": 3},
            },
        }
    )

    assert response is not None
    assert response["id"] == "call-1"
    result = response["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["args"] == {"text": "policy", "limit": 3}
    assert payload["conversation_id"].startswith("mcp-")
    assert calls[0][0] == "db-session"
    assert calls[0][1].session_id == "mcp"


@pytest.mark.asyncio
async def test_mcp_call_rejects_non_exposed_tool() -> None:
    with pytest.raises(mcp_server.JsonRpcError) as exc_info:
        await mcp_server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": "call-2",
                "method": "tools/call",
                "params": {"name": "generate_chart", "arguments": {}},
            }
        )

    assert exc_info.value.code == mcp_server.INVALID_PARAMS
