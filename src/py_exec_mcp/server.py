"""The py-exec MCP server: run Python with zero shell-quoting layers."""

from __future__ import annotations

from dataclasses import asdict

import anyio
from mcp.types import CallToolResult, ToolAnnotations

from py_exec_mcp.runner import (
    DEFAULT_TIMEOUT,
    execute,
    render,
    resolve_interpreter,
    resolve_workdir,
)

try:  # mcp 2.x renamed FastMCP to MCPServer. Nothing else this server touches moved.
    from mcp.server.mcpserver import MCPServer as _Server
except ModuleNotFoundError:  # mcp 1.x; mypy runs against 2.x, where this name does not exist
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[attr-defined, no-redef]

__all__ = ["build", "resolve_interpreter", "resolve_workdir"]

# camelCase keys validate on both majors: they are the field names on 1.x and aliases on 2.x.
ANNOTATIONS = ToolAnnotations.model_validate(
    {
        "title": "Run Python",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)

DESCRIPTION = """\
Run Python code and return what it printed, its stderr and its exit code.

Use this instead of `python -c` or a shell heredoc. The code travels as a JSON string and
reaches the interpreter over stdin, so no shell or argv parsing ever touches it. Write it
exactly as it would appear in a .py file: f-strings, nested quotes, backslashes, regexes and
triple-quoted blocks all arrive verbatim.

- State does not persist between calls. Each call is a fresh interpreter; define what you
  need every time.
- Only output comes back. print() what you want to see, with flush=True before anything slow.
- stdin is taken by the code itself, so the program cannot call input(); put data in the code.
- Runs in the working directory (PY_EXEC_CWD, else the server's cwd) with the project's own
  .venv if there is one, and that directory is on PYTHONPATH so local packages import.
- timeout_s (default 120) is clamped to [1, PY_EXEC_MAX_TIMEOUT]. On timeout the whole
  process tree is killed and whatever was printed so far is still returned.
- Long output is cut in the middle, keeping the start and the end, and the cut is announced
  with the number of characters omitted.

Structured content carries exit_code, stdout, stderr, timed_out, duration_s, interpreter
and workdir.
"""


def build() -> _Server:
    mcp = _Server("py-exec")

    @mcp.tool(description=DESCRIPTION, annotations=ANNOTATIONS)
    async def run_python(code: str, timeout_s: float = DEFAULT_TIMEOUT) -> CallToolResult:
        # In a worker thread: mcp 1.x calls sync tools inline, which would freeze the
        # whole server (pings included) for as long as the script runs.
        result = await anyio.to_thread.run_sync(execute, code, timeout_s)
        # Validated from the wire shape: the attribute is structuredContent on 1.x and
        # structured_content on 2.x, but both accept the camelCase key.
        return CallToolResult.model_validate(
            {
                "content": [{"type": "text", "text": render(result)}],
                "structuredContent": asdict(result),
            }
        )

    return mcp
