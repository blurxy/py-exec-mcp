"""Behaviour of the execution core, asserted on what a caller reads.

`runner.execute` is plain Python with no MCP in it, so these tests run the real
interpreter and check the real rendered text, never a mock.
"""

from __future__ import annotations

import sys

import pytest

from py_exec_mcp import runner


@pytest.fixture
def run(monkeypatch, tmp_path):
    monkeypatch.setenv("PY_EXEC_CWD", str(tmp_path))
    monkeypatch.setenv("PY_EXEC_PYTHON", sys.executable)
    return lambda code, **kw: runner.render(runner.execute(code, **kw))


# ── truncation keeps both ends and never holds more than the cap ──────────────


def test_bounded_buffer_keeps_head_and_tail_within_the_limit():
    """A 1 MB spew must not cost 1 MB of memory, and the end must survive."""
    buf = runner.BoundedBuffer(limit=200)
    total = 0
    for i in range(256):
        chunk = f"<{i:03d}>" + "x" * 4090 + "\n"
        buf.write(chunk)
        total += len(chunk)
    assert buf.dropped == total - buf.kept
    assert buf.kept <= 200
    text = buf.text("stdout")
    assert text.startswith("<000>")
    assert text.rstrip().endswith("x")
    assert f"stdout truncated: {buf.dropped:,} chars omitted here" in text


def test_truncation_is_announced_not_silent(run, monkeypatch):
    """A silently clipped result is indistinguishable from a short one."""
    monkeypatch.setattr(runner, "MAX_OUTPUT", 200)
    out = run("print('x' * 5000)")
    assert "stdout truncated:" in out
    assert "chars omitted here" in out


def test_truncated_output_keeps_the_tail_where_the_traceback_lives(run, monkeypatch):
    """The last lines are the ones that explain a failure. Head-only clipping loses them."""
    monkeypatch.setattr(runner, "MAX_OUTPUT", 200)
    out = run("print('x' * 5000)\nprint('THE END')\nraise SystemExit(7)")
    assert "THE END" in out
    assert "--- exit 7 ---" in out


# ── a timeout ends the whole process tree, not just the child ─────────────────


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import subprocess

        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
        ).stdout
        return str(pid) in out
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_timeout_kills_the_grandchildren_too(run, tmp_path):
    """Killing only the direct child leaves whatever it spawned running forever."""
    import time

    pid_file = tmp_path / "grandchild.pid"
    code = "\n".join(
        [
            "import subprocess, sys, time",
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])",
            f"open({str(pid_file)!r}, 'w').write(str(p.pid))",
            "print('spawned', flush=True)",
            "time.sleep(120)",
        ]
    )
    out = run(code, timeout_s=2)
    assert "--- TIMEOUT after 2s ---" in out
    grandchild = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while _alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not _alive(grandchild), "grandchild survived the timeout"
