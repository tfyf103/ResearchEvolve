from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_real_stdio_initialize_list_and_call(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "research_evolve.plugin.mcp_server", "--root", str(tmp_path)],
    )
    async with stdio_client(server) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "ResearchEvolve"
            tools = await session.list_tools()
            assert "research_project_create" in {item.name for item in tools.tools}
            response = await session.call_tool(
                "research_project_create",
                {"request_id": "stdio-create", "project_id": "stdio-project", "objective": "Test stdio MCP"},
            )
            assert not response.isError
            payload = response.structuredContent or json.loads(response.content[0].text)
            assert payload["status"] == "created"
            assert (tmp_path / "research-projects/stdio-project/research.json").is_file()
