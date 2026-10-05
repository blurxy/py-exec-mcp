"""What an MCP client reads from the server, over a real session.

Each test asserts on the CallToolResult or tool listing a client acts on, never on
"it did not crash". The execution core itself is covered in test_runner.py.
"""

from __future__ import annotations

import importlib.metadata
import os
import subprocess
import sys
import time
from pathlib import Path

import anyio
import pytest

from py_exec_mcp import server

pytestmark = pytest.mark.anyio


def text_of(result) -> str:
    return "".join(block.text for block in result.content if block.type == "text")


# ── the reason this server exists, end to end ─────────────────────────────────

NASTY = "\n".join(
    [
        'name = "world"',
        """print(f'he said "hello {name}" and it held')""",
        r'print(r"C:\demo\x\n is not a newline")',
        "print('nested ' + \"quotes \" + '''and triples''')",
    ]
)


async def test_nested_quotes_and_fstrings_survive_verbatim(session):
    """THE POINT OF THE PROJECT. Through a shell this mangles; over JSON and stdin it must not."""
    out = text_of(await session.call_tool("run_python", {"code": NASTY}))
    assert 'he said "hello world" and it held' in out
    assert r"C:\demo\x\n is not a newline" in out
    assert "nested quotes and triples" in out


# ── the contract a caller gets ────────────────────────────────────────────────


async def test_result_carries_structured_content_alongside_the_text(session):
    """Programs want fields; models want text. Both come back from one call."""
    result = await session.call_tool("run_python", {"code": "import sys; print('v'); sys.exit(3)"})
    assert "--- exit 3 ---" in text_of(result)
    # 1.x and 2.x spell the attribute differently; the wire name is the same.
    fields = result.model_dump(by_alias=True)["structuredContent"]
    assert fields["exit_code"] == 3
    assert fields["stdout"].strip() == "v"
    assert fields["timed_out"] is False
    assert fields["interpreter"] == sys.executable


async def test_annotations_say_it_is_destructive_and_open_world(session):
    """Clients that gate on hints must not be told this tool is read-only."""
    tools = (await session.list_tools()).tools
    hints = next(t for t in tools if t.name == "run_python").annotations
    assert hints is not None
    hints = hints.model_dump(by_alias=True)
    assert hints["destructiveHint"] is True
    assert hints["openWorldHint"] is True
    assert hints["readOnlyHint"] is False


async def test_description_tells_the_caller_what_bites(session):
    """The description is the only manual an LLM reads."""
    tools = (await session.list_tools()).tools
    desc = next(t for t in tools if t.name == "run_python").description.lower()
    assert "does not persist" in desc
    assert "print(" in desc
    assert "stdin" in desc


async def test_a_running_script_does_not_block_the_server(session):
    """While one call sleeps, the server must still answer. mcp 1.x runs sync tools inline.

    The clock starts before the call: in a single-threaded harness a blocked loop also
    delays this test's own sleep, so the whole span is what measures responsiveness.
    """
    started = time.monotonic()
    async with anyio.create_task_group() as tg:
        tg.start_soon(session.call_tool, "run_python", {"code": "import time; time.sleep(3)"})
        await anyio.sleep(0.2)  # let the request reach the server
        await session.send_ping()
        responsive_after = time.monotonic() - started
    assert responsive_after < 1.5, f"ping answered only after {responsive_after:.1f}s"


# ── SDK compatibility ─────────────────────────────────────────────────────────


def test_binds_the_server_class_the_installed_sdk_provides():
    """mcp 2.x renamed FastMCP to MCPServer; pinning either name breaks half the users.

    The expectation is read from the installed distribution, NOT from server.py's
    import — so re-hardcoding one major goes red on a box running the other.
    """
    major = int(importlib.metadata.version("mcp").split(".")[0])
    expected = "MCPServer" if major >= 2 else "FastMCP"
    assert type(server.build()).__name__ == expected


# ── the packaged entry point ──────────────────────────────────────────────────


def test_module_entry_point_imports_without_starting_a_server():
    """`python -m py_exec_mcp` must at least import cleanly on every platform."""
    src = Path(__file__).resolve().parents[1] / "src"
    probe = "import py_exec_mcp, py_exec_mcp.__main__; print(py_exec_mcp.__version__)"
    r = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONPATH": str(src)},
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == importlib.metadata.version("py-exec-mcp")
