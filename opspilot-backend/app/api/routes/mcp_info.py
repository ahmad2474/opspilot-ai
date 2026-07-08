"""Read-only introspection of the MCP server (Phase 7) for the frontend.

This is the one route that reaches into app.mcp instead of app.services —
it exists purely to prove the MCP server is real by listing its actual
registered tools at request time, not a hardcoded copy of the tool list
that could drift out of sync.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.mcp.server import mcp as mcp_server
from app.models.mcp_info import McpServerInfo, McpToolInfo

router = APIRouter()


@router.get("/mcp/tools", response_model=McpServerInfo)
async def get_mcp_tools() -> McpServerInfo:
    tools = await mcp_server.list_tools()
    return McpServerInfo(
        server_name=mcp_server.name,
        transport="stdio (JSON-RPC 2.0)",
        tool_count=len(tools),
        tools=[McpToolInfo(name=t.name, description=t.description) for t in tools],
    )
