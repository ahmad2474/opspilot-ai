from __future__ import annotations

from pydantic import BaseModel


class McpToolInfo(BaseModel):
    name: str
    description: str | None = None


class McpServerInfo(BaseModel):
    server_name: str
    transport: str
    tool_count: int
    tools: list[McpToolInfo]
