"""Verify the exact packaged Node runtime and pinned MCP probe server."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROBE_TOOL = "collie_bundle_probe"


def runtime_root() -> Path:
    configured = os.environ.get("COLLIE_MCP_RUNTIME_ROOT", "").strip()
    if configured:
        return Path(configured).resolve()
    return (Path(__file__).resolve().parents[3] / "mcp-runtime").resolve()


def packaged_probe_paths(root: Path | None = None) -> tuple[Path, Path]:
    base = (root or runtime_root()).resolve()
    node = base / "node" / ("node.exe" if sys.platform == "win32" else "bin/node")
    server = base / "servers" / "mcp-probe-server" / "server.mjs"
    return node, server


async def probe_packaged_mcp(root: Path | None = None) -> list[str]:
    node, server = packaged_probe_paths(root)
    if not node.is_file():
        raise RuntimeError(f"Packaged Node runtime is missing: {node}")
    if not server.is_file():
        raise RuntimeError(f"Packaged MCP probe server is missing: {server}")

    params = StdioServerParameters(command=str(node), args=[str(server)])
    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        result = await session.list_tools()
        names = [tool.name for tool in result.tools]
        if PROBE_TOOL not in names:
            raise RuntimeError("Packaged MCP server did not advertise its probe tool.")
        called = await session.call_tool(PROBE_TOOL, arguments={})
        if called.isError:
            raise RuntimeError("Packaged MCP probe tool returned an error.")
        return names


def main() -> None:
    names = asyncio.run(probe_packaged_mcp())
    print(f"Collie packaged MCP probe OK: {', '.join(names)}")


if __name__ == "__main__":
    main()
