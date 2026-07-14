from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_get_mcp_tools_lists_real_registered_tools(auth_headers: dict[str, str]) -> None:
    """No mocking — this is meant to introspect the real MCP server, so the
    test asserts against its actual registered tool set."""
    response = client.get("/mcp/tools", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["server_name"] == "opspilot"
    assert body["transport"] == "stdio (JSON-RPC 2.0)"
    assert body["tool_count"] == len(body["tools"])
    tool_names = {t["name"] for t in body["tools"]}
    assert "list_ec2_instances" in tool_names
    assert "find_similar_past_investigations" in tool_names


def test_get_mcp_tools_requires_session() -> None:
    response = client.get("/mcp/tools")

    assert response.status_code == 401
