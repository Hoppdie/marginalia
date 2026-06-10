"""Minimal stdio MCP server exposing Marginalia read-only retrieval tools."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping
from typing import Any, TextIO
from uuid import uuid4

from marginalia.agent.tools import ToolContext, ToolRegistration, all_tool_defs, get_tool
from marginalia.db.session import session_scope

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "marginalia"

READ_ONLY_TOOL_NAMES: tuple[str, ...] = (
    "recall_knowledge",
    "read_files",
    "search_metadata",
    "search_journal",
    "read_entries_metadata",
    "list_folder",
    "list_catalogs",
    "read_catalog",
    "resolve_tag",
    "materialize_view",
)
READ_ONLY_TOOL_SET = set(READ_ONLY_TOOL_NAMES)

JSONRPC_VERSION = "2.0"
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _server_version() -> str:
    try:
        from marginalia import __version__
    except Exception:  # noqa: BLE001
        return "unknown"
    return __version__


def _jsonrpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _jsonrpc_error(
    request_id: Any,
    code: int,
    message: str,
    data: Any | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def _text_content(payload: Any) -> list[dict[str, str]]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return [{"type": "text", "text": text}]


def _mcp_tool(reg: ToolRegistration) -> dict[str, Any]:
    return {
        "name": reg.name,
        "description": reg.description,
        "inputSchema": reg.input_schema,
    }


def list_mcp_tools() -> list[dict[str, Any]]:
    by_name = {
        tool_def.name: tool_def
        for tool_def in all_tool_defs()
        if tool_def.name in READ_ONLY_TOOL_SET
    }
    return [
        {
            "name": name,
            "description": by_name[name].description,
            "inputSchema": by_name[name].input_schema,
        }
        for name in READ_ONLY_TOOL_NAMES
        if name in by_name
    ]


async def call_mcp_tool(name: str, arguments: Mapping[str, Any] | None) -> dict[str, Any]:
    if name not in READ_ONLY_TOOL_SET:
        raise JsonRpcError(INVALID_PARAMS, f"tool is not exposed over MCP: {name}")
    reg = get_tool(name)
    if reg is None:
        raise JsonRpcError(INVALID_PARAMS, f"unknown tool: {name}")
    args = dict(arguments or {})
    ctx = ToolContext(
        session_id="mcp",
        conversation_id=f"mcp-{uuid4().hex}",
        user_message=str(args.get("query") or args.get("text") or ""),
    )
    async with session_scope() as db:
        result = await reg.handler(db, ctx, args)
    return {
        "content": _text_content(result),
        "isError": bool(isinstance(result, dict) and result.get("error")),
    }


async def handle_message(message: Mapping[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    if method is None:
        raise JsonRpcError(INVALID_REQUEST, "missing method")
    if not isinstance(method, str):
        raise JsonRpcError(INVALID_REQUEST, "method must be a string")

    is_notification = "id" not in message
    params = message.get("params") or {}
    if params is not None and not isinstance(params, Mapping):
        raise JsonRpcError(INVALID_PARAMS, "params must be an object")

    if method == "initialize":
        if is_notification:
            return None
        return _jsonrpc_result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": _server_version(),
                },
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return None if is_notification else _jsonrpc_result(request_id, {})
    if method == "tools/list":
        return None if is_notification else _jsonrpc_result(
            request_id,
            {"tools": list_mcp_tools()},
        )
    if method == "tools/call":
        if is_notification:
            return None
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise JsonRpcError(INVALID_PARAMS, "tools/call requires params.name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, Mapping):
            raise JsonRpcError(INVALID_PARAMS, "tools/call params.arguments must be an object")
        try:
            result = await call_mcp_tool(name, arguments)
        except JsonRpcError:
            raise
        except Exception as exc:  # noqa: BLE001
            result = {
                "content": _text_content({"error": f"{type(exc).__name__}: {exc}"}),
                "isError": True,
            }
        return _jsonrpc_result(request_id, result)

    raise JsonRpcError(METHOD_NOT_FOUND, f"unknown method: {method}")


async def _read_line(stdin: TextIO) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, stdin.readline)


async def run_stdio_server(
    *,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> int:
    while True:
        line = await _read_line(stdin)
        if not line:
            return 0
        line = line.strip()
        if not line:
            continue
        request_id: Any = None
        try:
            message = json.loads(line)
            if not isinstance(message, Mapping):
                raise JsonRpcError(INVALID_REQUEST, "message must be a JSON object")
            request_id = message.get("id")
            response = await handle_message(message)
        except json.JSONDecodeError as exc:
            response = _jsonrpc_error(None, PARSE_ERROR, "invalid JSON", str(exc))
        except JsonRpcError as exc:
            response = _jsonrpc_error(request_id, exc.code, exc.message, exc.data)
        except Exception as exc:  # noqa: BLE001
            response = _jsonrpc_error(
                request_id,
                INTERNAL_ERROR,
                f"{type(exc).__name__}: {exc}",
            )
        if response is None:
            continue
        stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        stdout.flush()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="marginalia mcp",
        description="Run a stdio MCP server exposing read-only Marginalia retrieval tools.",
    )
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Run over stdio. This is the only transport currently supported.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    parser.parse_args(argv)
    return asyncio.run(run_stdio_server())


if __name__ == "__main__":
    raise SystemExit(main())
