"""The py-exec MCP server: run Python with zero shell-quoting layers."""

from __future__ import annotations

from py_exec_mcp.runner import (
    DEFAULT_TIMEOUT,
    execute,
    render,
    resolve_interpreter,
    resolve_workdir,
)

try:  # mcp 2.x renamed FastMCP to MCPServer. Nothing else this server touches moved.
    from mcp.server.mcpserver import MCPServer as _Server
except ModuleNotFoundError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

__all__ = ["build", "resolve_interpreter", "resolve_workdir"]


def build() -> _Server:
    mcp = _Server("py-exec")

    @mcp.tool()
    def run_python(code: str, timeout_s: float = DEFAULT_TIMEOUT) -> str:
        """Execute Python code and return its stdout, stderr and exit code.

        The code is fed to the interpreter over STDIN, so it is never parsed by a
        shell or split into argv. Write it exactly as it would appear in a .py file:
        f-strings, nested quotes and backslashes all survive verbatim.

        Runs in the working directory (PY_EXEC_CWD or cwd), which is also prepended
        to PYTHONPATH so the project's own packages import. timeout_s is clamped to
        [1, PY_EXEC_MAX_TIMEOUT]; on timeout, whatever was produced is still returned.
        """
        return render(execute(code, timeout_s))

    return mcp
