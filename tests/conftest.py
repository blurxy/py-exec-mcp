"""Shared fixtures: a real MCP client session wired to the server over memory streams.

Protocol-level tests read exactly what a client reads, on whichever SDK major is
installed. The async tests run on anyio's pytest plugin, which mcp already depends
on, pinned to the asyncio backend.
"""

from __future__ import annotations

import contextlib
import sys

import pytest

from py_exec_mcp import server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@contextlib.asynccontextmanager
async def connect(mcp_server):
    try:  # mcp 1.x ships a one-call helper
        from mcp.shared.memory import create_connected_server_and_client_session
    except ImportError:
        create_connected_server_and_client_session = None

    if create_connected_server_and_client_session is not None:
        async with create_connected_server_and_client_session(mcp_server) as session:
            yield session
        return

    # mcp 2.x: wire the low-level server to a ClientSession by hand.
    import anyio
    from mcp.client.session import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    low = mcp_server._lowlevel_server
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        client_read, client_write = client_streams
        server_read, server_write = server_streams
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                low.run,
                server_read,
                server_write,
                low.create_initialization_options(),
                True,
            )
            async with ClientSession(client_read, client_write) as session:
                await session.initialize()
                yield session
            tg.cancel_scope.cancel()


@pytest.fixture
async def session(monkeypatch, tmp_path):
    """A connected client, with the workdir pinned to a temp dir."""
    monkeypatch.setenv("PY_EXEC_CWD", str(tmp_path))
    monkeypatch.setenv("PY_EXEC_PYTHON", sys.executable)
    async with connect(server.build()) as s:
        yield s
