from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.mcp_client.client import (
    MCPClient,
    MCPConfigError,
    MCPConnectionError,
    MCPServerConfig,
    load_server_configs,
)


def _write_config(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_load_stdio_server_config(tmp_path: Path) -> None:
    config_file = tmp_path / "servers.json"
    _write_config(
        config_file,
        {
            "servers": {
                "local": {
                    "command": "/usr/bin/python3",
                    "args": ["-m", "some.server"],
                    "env": {"EXAMPLE": "value"},
                    "connect_timeout": 5,
                }
            }
        },
    )

    servers = load_server_configs(config_file)

    assert len(servers) == 1
    assert servers[0].name == "local"
    assert servers[0].command == "/usr/bin/python3"
    assert servers[0].args == ("-m", "some.server")
    assert servers[0].env == {"EXAMPLE": "value"}
    assert servers[0].connect_timeout == 5.0


def test_load_multiple_servers_from_array(tmp_path: Path) -> None:
    config_file = tmp_path / "servers.json"
    _write_config(
        config_file,
        {
            "servers": [
                {"name": "one", "command": "/bin/one"},
                {"name": "two", "command": "/bin/two", "args": ["--x"]},
            ]
        },
    )

    servers = load_server_configs(config_file)

    assert [server.name for server in servers] == ["one", "two"]
    assert servers[1].args == ("--x",)


def test_missing_config_file_is_clear(tmp_path: Path) -> None:
    with pytest.raises(MCPConfigError, match="not found"):
        load_server_configs(tmp_path / "missing.json")


def test_unsupported_transport_is_rejected(tmp_path: Path) -> None:
    config_file = tmp_path / "servers.json"
    _write_config(
        config_file,
        {
            "servers": {
                "remote": {
                    "transport": "streamable-http",
                    "command": "/bin/remote",
                }
            }
        },
    )

    with pytest.raises(MCPConfigError, match="unsupported transport"):
        load_server_configs(config_file)


def test_empty_server_list_is_rejected() -> None:
    with pytest.raises(MCPConfigError, match="At least one"):
        MCPClient([])


@pytest.mark.asyncio
async def test_unavailable_server_raises_connection_error() -> None:
    client = MCPClient(
        [
            MCPServerConfig(
                name="missing",
                command="/definitely/not/a/real/mcp-server",
                connect_timeout=3.0,
            )
        ]
    )

    with pytest.raises(MCPConnectionError, match="missing"):
        await client.connect()

    assert client.connected is False
    assert await _list_tools_fails(client) is True


async def _list_tools_fails(client: MCPClient) -> bool:
    try:
        await client.list_tools()
    except MCPConnectionError:
        return True
    return False
