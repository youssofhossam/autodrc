"""Connection and tool-access layer for local MCP servers.

This module deliberately has no dependency on Google Gemini (or any other LLM).
It only knows the MCP protocol and returns plain Python data structures.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


class MCPError(RuntimeError):
    """Base class for errors raised by the MCP layer."""


class MCPConfigError(MCPError):
    """The local MCP server configuration file is missing or malformed."""


class MCPConnectionError(MCPError):
    """An MCP server could not be started, connected to, or reached."""


class MCPToolError(MCPError):
    """An MCP tool call failed or referenced an unknown tool."""


@dataclass(frozen=True)
class MCPServerConfig:
    """Everything needed to launch one STDIO MCP server."""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None
    cwd: str | None = None
    transport: str = "stdio"
    connect_timeout: float = 20.0


@dataclass(frozen=True)
class MCPTool:
    """A tool discovered from an MCP server.

    `name` is the name the MCP server uses. It is intentionally not renamed
    here; LLM-specific name mangling belongs in the LLM layer.
    """

    server: str
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """Provider-neutral result of one MCP tool call."""

    text: str
    is_error: bool = False


@dataclass
class _Request:
    operation: str
    future: asyncio.Future[Any]
    name: str | None = None
    arguments: dict[str, Any] | None = None


class _ServerConnection:
    """Owns one MCP SDK connection inside a single long-lived asyncio task.

    The MCP SDK's stdio transport is an AnyIO context manager. AnyIO cancel
    scopes must be entered and exited by the same task, so we cannot open the
    connection in one task and close it from another. This class creates a
    dedicated "owner" task that keeps the transport open and processes
    list/call requests from a queue.
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._queue: asyncio.Queue[_Request | None] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._startup_error: BaseException | None = None
        self._session: ClientSession | None = None

    @property
    def is_alive(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self._task is not None:
            return

        self._ready = asyncio.Event()
        self._startup_error = None
        self._task = asyncio.create_task(
            self._run(), name=f"mcp-server-{self.config.name}"
        )

        try:
            await asyncio.wait_for(
                self._ready.wait(), timeout=self.config.connect_timeout
            )
        except TimeoutError:
            await self._stop_task()
            raise MCPConnectionError(
                f"MCP server '{self.config.name}' did not become ready within "
                f"{self.config.connect_timeout:g}s."
            ) from None

        if self._startup_error is not None:
            error = self._startup_error
            await self._stop_task()
            raise MCPConnectionError(
                f"Could not connect to MCP server '{self.config.name}': "
                f"{_describe_exception(error)}"
            ) from error

    async def _run(self) -> None:
        try:
            params = StdioServerParameters(
                command=self.config.command,
                args=list(self.config.args),
                env=self.config.env,
                cwd=self.config.cwd,
            )
            logger.info(
                "Starting MCP server '%s': %s %s",
                self.config.name,
                self.config.command,
                " ".join(self.config.args),
            )
            async with (
                stdio_client(params) as (read_stream, write_stream),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                self._session = session
                logger.info("MCP server '%s' is ready.", self.config.name)
                self._ready.set()
                await self._serve(session)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._startup_error = exc
            logger.exception("MCP server '%s' stopped unexpectedly.", self.config.name)
        finally:
            self._session = None
            self._ready.set()
            self._fail_pending(
                MCPConnectionError(f"MCP server '{self.config.name}' is not connected.")
            )

    async def _serve(self, session: ClientSession) -> None:
        while True:
            request = await self._queue.get()
            if request is None:
                return
            if request.future.cancelled():
                continue
            try:
                if request.operation == "list_tools":
                    result = await self._list_all_tools(session)
                elif request.operation == "call_tool":
                    result = await session.call_tool(
                        request.name or "", arguments=request.arguments or {}
                    )
                else:  # pragma: no cover - internal programming error
                    raise MCPError(f"Unknown MCP request: {request.operation}")
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 - delivered to caller
                if not request.future.done():
                    request.future.set_exception(exc)
            else:
                if not request.future.done():
                    request.future.set_result(result)

    async def _list_all_tools(self, session: ClientSession) -> list[Any]:
        tools: list[Any] = []
        cursor: str | None = None
        while True:
            result = await session.list_tools(cursor=cursor)
            tools.extend(result.tools)
            cursor = getattr(result, "nextCursor", None)
            if not cursor:
                return tools

    async def _request(self, request: _Request) -> Any:
        if self._task is None or self._task.done():
            raise MCPConnectionError(
                f"MCP server '{self.config.name}' is not connected."
            )
        if self._startup_error is not None:
            raise MCPConnectionError(
                f"MCP server '{self.config.name}' is not connected: "
                f"{_describe_exception(self._startup_error)}"
            )
        await self._queue.put(request)
        return await request.future

    async def list_tools(self) -> list[Any]:
        loop = asyncio.get_running_loop()
        request = _Request(operation="list_tools", future=loop.create_future())
        return await self._request(request)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        loop = asyncio.get_running_loop()
        request = _Request(
            operation="call_tool",
            future=loop.create_future(),
            name=name,
            arguments=arguments,
        )
        return await self._request(request)

    def _fail_pending(self, error: MCPConnectionError) -> None:
        while True:
            try:
                request = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if request is not None and not request.future.done():
                request.future.set_exception(error)

    async def close(self) -> None:
        if self._task is None:
            return
        if not self._task.done():
            await self._queue.put(None)
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except TimeoutError:
                logger.warning(
                    "MCP server '%s' did not shut down cleanly; cancelling.",
                    self.config.name,
                )
        await self._stop_task()

    async def _stop_task(self) -> None:
        task = self._task
        if task is None:
            return
        self._task = None
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            logger.debug(
                "MCP server '%s' task ended during shutdown.",
                self.config.name,
                exc_info=True,
            )


class MCPClient:
    """Manages one or more MCP servers and exposes their discovered tools."""

    def __init__(self, servers: Sequence[MCPServerConfig]) -> None:
        if not servers:
            raise MCPConfigError("At least one MCP server must be configured.")
        names = [server.name for server in servers]
        if len(set(names)) != len(names):
            raise MCPConfigError("MCP server names must be unique.")
        self._configs = tuple(servers)
        self._connections: dict[str, _ServerConnection] = {}
        self._tools: list[MCPTool] = []
        self._connect_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return bool(self._connections) and all(
            connection.is_alive for connection in self._connections.values()
        )

    async def connect(self) -> None:
        """Start every configured server and discover its tools.

        Connection is all-or-nothing: if one server fails, already-started
        servers are closed so the application does not run half-configured.
        """

        async with self._connect_lock:
            if self._connections:
                if self.connected:
                    return
                # The server task died (crash, closed pipe, ...). Drop the
                # stale connections and start fresh ones.
                await self.close()
            started: dict[str, _ServerConnection] = {}
            try:
                for config in self._configs:
                    connection = _ServerConnection(config)
                    await connection.start()
                    started[config.name] = connection
                self._connections = started
                await self.refresh_tools()
            except BaseException:
                self._connections = {}
                for connection in started.values():
                    await connection.close()
                raise

    async def refresh_tools(self) -> list[MCPTool]:
        """Re-discover tools on every connected server."""

        discovered: list[MCPTool] = []
        for server_name, connection in self._connections.items():
            raw_tools = await connection.list_tools()
            for raw_tool in raw_tools:
                schema = getattr(raw_tool, "inputSchema", None)
                discovered.append(
                    MCPTool(
                        server=server_name,
                        name=str(getattr(raw_tool, "name", "")),
                        description=str(getattr(raw_tool, "description", "") or ""),
                        input_schema=dict(schema)
                        if isinstance(schema, Mapping)
                        else {},
                    )
                )
        self._tools = discovered
        logger.info(
            "Discovered %d MCP tool(s) across %d server(s).",
            len(discovered),
            len(self._connections),
        )
        return list(discovered)

    async def list_tools(self) -> list[MCPTool]:
        """Return fresh tool definitions from all servers."""

        if not self._connections:
            raise MCPConnectionError("The MCP client is not connected.")
        return await self.refresh_tools()

    async def call_tool(self, tool: MCPTool, arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool that was previously discovered.

        Passing the whole MCPTool (rather than just a name) makes it explicit
        that only discovered tools may be executed.
        """

        if not any(
            known.server == tool.server and known.name == tool.name
            for known in self._tools
        ):
            raise MCPToolError(
                f"Tool '{tool.name}' was not discovered from any connected MCP server."
            )

        connection = self._connections.get(tool.server)
        if connection is None:
            raise MCPConnectionError(f"MCP server '{tool.server}' is not connected.")

        try:
            raw_result = await connection.call_tool(tool.name, arguments)
        except (MCPError, asyncio.CancelledError):
            raise
        except BaseException as exc:
            raise MCPToolError(
                f"MCP tool '{tool.name}' failed: {_describe_exception(exc)}"
            ) from exc

        return _convert_tool_result(raw_result)

    async def close(self) -> None:
        connections = list(self._connections.values())
        self._connections = {}
        self._tools = []
        for connection in connections:
            try:
                await connection.close()
            except Exception:
                logger.exception(
                    "Error while closing MCP server '%s'.", connection.config.name
                )


def _describe_exception(exc: BaseException) -> str:
    """Return a useful one-line error without dumping a giant traceback."""

    if isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        return "; ".join(_describe_exception(item) for item in exc.exceptions)
    text = str(exc).strip()
    if text:
        return f"{type(exc).__name__}: {text}"
    return type(exc).__name__


def _convert_tool_result(raw_result: Any) -> ToolResult:
    """Turn an MCP CallToolResult into a provider-neutral ToolResult."""

    parts: list[str] = []
    for block in getattr(raw_result, "content", None) or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            parts.append(str(getattr(block, "text", "")))
        elif block_type == "image":
            mime = getattr(block, "mimeType", "unknown")
            parts.append(f"[image content: {mime}]")
        elif block_type == "audio":
            mime = getattr(block, "mimeType", "unknown")
            parts.append(f"[audio content: {mime}]")
        elif block_type == "resource":
            resource = getattr(block, "resource", None)
            uri = getattr(resource, "uri", None)
            text = getattr(resource, "text", None)
            if text is not None:
                parts.append(f"[resource {uri}]\n{text}")
            else:
                blob = getattr(resource, "blob", None)
                size = len(blob) if isinstance(blob, str) else "unknown"
                parts.append(f"[binary resource {uri}: {size} base64 chars]")
        else:
            parts.append(f"[{block_type or 'unknown'} content]")

    structured = getattr(raw_result, "structuredContent", None)
    if structured is not None and not parts:
        parts.append(
            "Structured content: "
            + json.dumps(structured, ensure_ascii=False, default=str)
        )

    return ToolResult(
        text="\n".join(part for part in parts if part).strip() or "(empty result)",
        is_error=bool(getattr(raw_result, "isError", False)),
    )


def load_server_configs(path: str | Path) -> list[MCPServerConfig]:
    """Load one or more STDIO MCP servers from a JSON file.

    Example:
        {
          "servers": {
            "example": {
              "transport": "stdio",
              "command": "/path/to/python",
              "args": ["-m", "my_server"],
              "env": {},
              "cwd": null
            }
          }
        }
    """

    config_path = Path(path).expanduser()
    if not config_path.is_file():
        raise MCPConfigError(f"MCP configuration file not found: {config_path}")

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MCPConfigError(
            f"Could not read MCP configuration file {config_path}: {exc}"
        ) from exc

    if not isinstance(data, Mapping):
        raise MCPConfigError("MCP configuration must be a JSON object.")
    servers = data.get("servers")
    if isinstance(servers, Mapping):
        entries = list(servers.items())
    elif isinstance(servers, list):
        entries = [
            (entry.get("name") if isinstance(entry, Mapping) else None, entry)
            for entry in servers
        ]
    else:
        raise MCPConfigError(
            "MCP configuration must contain a 'servers' object or array."
        )
    if not entries:
        raise MCPConfigError("MCP configuration contains no servers.")

    configs: list[MCPServerConfig] = []
    for raw_name, raw in entries:
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise MCPConfigError("Every MCP server needs a non-empty name.")
        if not isinstance(raw, Mapping):
            raise MCPConfigError(f"MCP server '{raw_name}' must be an object.")

        transport = str(raw.get("transport", "stdio")).strip().lower()
        if transport != "stdio":
            raise MCPConfigError(
                f"MCP server '{raw_name}' uses unsupported transport "
                f"'{transport}'. This project currently implements STDIO; "
                "add another transport in app/mcp_client/client.py."
            )

        command = raw.get("command")
        if not isinstance(command, str) or not command.strip():
            raise MCPConfigError(
                f"MCP server '{raw_name}' needs a non-empty 'command'."
            )

        raw_args = raw.get("args", [])
        if not isinstance(raw_args, list) or not all(
            isinstance(arg, str) for arg in raw_args
        ):
            raise MCPConfigError(
                f"MCP server '{raw_name}' has 'args' that are not a list of strings."
            )

        raw_env = raw.get("env") or {}
        if not isinstance(raw_env, Mapping):
            raise MCPConfigError(f"MCP server '{raw_name}' has an invalid 'env'.")
        env: dict[str, str] = {}
        for key, value in raw_env.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise MCPConfigError(
                    f"MCP server '{raw_name}' environment keys and values "
                    "must be strings."
                )
            env[key] = os.path.expandvars(value)

        cwd = raw.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            raise MCPConfigError(f"MCP server '{raw_name}' has an invalid 'cwd'.")

        timeout = raw.get("connect_timeout", 20.0)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise MCPConfigError(
                f"MCP server '{raw_name}' has an invalid 'connect_timeout'."
            )

        configs.append(
            MCPServerConfig(
                name=raw_name,
                command=os.path.expandvars(os.path.expanduser(command)),
                args=tuple(os.path.expandvars(arg) for arg in raw_args),
                env=env or None,
                cwd=os.path.expandvars(os.path.expanduser(cwd)) if cwd else None,
                transport=transport,
                connect_timeout=float(timeout),
            )
        )

    return configs
