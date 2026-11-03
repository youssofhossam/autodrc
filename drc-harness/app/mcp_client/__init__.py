"""MCP client layer: connects to configured servers and exposes their tools."""

from app.mcp_client.client import (
    MCPClient,
    MCPConfigError,
    MCPConnectionError,
    MCPError,
    MCPServerConfig,
    MCPTool,
    MCPToolError,
    ToolResult,
    load_server_configs,
)

__all__ = [
    "MCPClient",
    "MCPConfigError",
    "MCPConnectionError",
    "MCPError",
    "MCPServerConfig",
    "MCPTool",
    "MCPToolError",
    "ToolResult",
    "load_server_configs",
]
