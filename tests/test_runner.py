"""Behaviour of the execution core, asserted on what a caller reads.

`runner.execute` is plain Python with no MCP in it, so these tests run the real
interpreter and check the real rendered text, never a mock.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from py_exec_mcp import runner


@pytest.fixture
def run(monkeypatch, tmp_path):
    monkeypatch.setenv("PY_EXEC_CWD", str(tmp_path))
    monkeypatch.setenv("PY_EXEC_PYTHON", sys.executable)
    return lambda code, **kw: runner.render(runner.execute(code, **kw))


# ── what comes back ───────────────────────────────────────────────────────────


def test_exit_code_reaches_the_caller(run):
    assert "--- exit 3 ---" in run("import sys; sys.exit(3)")
    assert "--- exit 0 ---" in run("print('ok')")


def test_stderr_is_captured_and_labelled(run):
    out = run("import sys; print('to-out'); print('to-err', file=sys.stderr)")
    assert "to-out" in out
    assert "--- stderr ---" in out
    assert "to-err" in out


def test_a_traceback_comes_back_rather_than_vanishing(run):
    out = run("raise ValueError('boom')")
    assert "ValueError: boom" in out
    assert "--- exit 1 ---" in out


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


# ── timeouts ──────────────────────────────────────────────────────────────────


def test_timeout_returns_what_was_produced(run):
    """The runs that time out are the ones whose partial output matters most."""
    code = "import time\nprint('before the hang', flush=True)\ntime.sleep(30)"
    out = run(code, timeout_s=2)
    assert "--- TIMEOUT after 2s ---" in out
    assert "before the hang" in out, "partial output was discarded on timeout"


def test_timeout_is_clamped_at_both_ends(run):
    assert "--- exit 0 ---" in run("print('fast')", timeout_s=0.001)
    assert "--- exit 0 ---" in run("print('fast')", timeout_s=10**9)


def _alive(pid: int, run=subprocess.run) -> bool:
    if sys.platform == "win32":
        out = run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_timeout_kills_the_grandchildren_too(run, tmp_path):
    """Killing only the direct child leaves whatever it spawned running forever."""
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


def test_timeout_still_kills_the_child_when_the_tree_kill_tool_is_missing(
    run, tmp_path, monkeypatch
):
    """A missing taskkill or a failing killpg must not turn a timeout into a leaked process."""
    real_run = subprocess.run

    def boom(*args, **kwargs):
        raise FileNotFoundError("no such tool")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(os, "killpg", boom, raising=False)
    pid_file = tmp_path / "child.pid"
    code = chr(10).join(
        [
            "import os, time",
            f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))",
            "time.sleep(120)",
        ]
    )
    out = run(code, timeout_s=2)
    assert "--- TIMEOUT after 2s ---" in out
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while _alive(child, run=real_run) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not _alive(child, run=real_run), "child survived because the tree kill tool failed"


# ── resolution: the portability half ──────────────────────────────────────────


def test_a_missing_interpreter_reports_itself(monkeypatch, tmp_path):
    monkeypatch.setenv("PY_EXEC_CWD", str(tmp_path))
    monkeypatch.setenv("PY_EXEC_PYTHON", str(tmp_path / "definitely-not-here"))
    assert "could not start interpreter" in runner.render(runner.execute("print(1)"))


def test_explicit_interpreter_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("PY_EXEC_PYTHON", "/custom/python")
    assert runner.resolve_interpreter(tmp_path) == Path("/custom/python")


@pytest.mark.parametrize("layout", ["Scripts/python.exe", "bin/python", "bin/python3"])
def test_both_venv_layouts_are_found(monkeypatch, tmp_path, layout):
    """Windows puts it in Scripts/, everyone else in bin/. Hardcoding one is the bug."""
    monkeypatch.delenv("PY_EXEC_PYTHON", raising=False)
    target = tmp_path / ".venv" / layout
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("")
    assert runner.resolve_interpreter(tmp_path) == target


def test_falls_back_to_the_running_interpreter(monkeypatch, tmp_path):
    monkeypatch.delenv("PY_EXEC_PYTHON", raising=False)
    assert runner.resolve_interpreter(tmp_path) == Path(sys.executable)


def test_workdir_is_never_derived_from_the_package_location(monkeypatch, tmp_path):
    """Once pip-installed, a path relative to __file__ points into site-packages."""
    monkeypatch.setenv("PY_EXEC_CWD", str(tmp_path))
    resolved = runner.resolve_workdir()
    assert resolved == tmp_path.resolve()
    assert Path(runner.__file__).parent not in resolved.parents


def test_code_runs_in_the_workdir_and_can_import_from_it(run, tmp_path):
    (tmp_path / "local_module.py").write_text("VALUE = 'imported-from-workdir'\n")
    out = run("import local_module; print(local_module.VALUE)")
    assert "imported-from-workdir" in out


# ── the result explains the failures an agent hits most ───────────────────────


def test_a_missing_module_names_the_interpreter_and_the_fix(run):
    """ModuleNotFoundError almost always means "wrong interpreter", so say which one ran."""
    out = run("import definitely_not_installed_pkg_xyz")
    assert "ModuleNotFoundError" in out
    assert sys.executable in out, "the interpreter that lacked the module is not named"
    assert "PY_EXEC_PYTHON" in out, "the caller is not told how to point at another interpreter"


@pytest.mark.parametrize("value", ["lots", "0", "-5", "nan", "inf"])
def test_a_bad_config_value_names_the_variable_at_startup(value):
    """`int('abc')` from inside a module import tells nobody which setting was wrong,
    and a cap of 0 or -5 is a misconfiguration too, not a request for one character."""
    r = subprocess.run(
        [sys.executable, "-c", "import py_exec_mcp.runner"],
        capture_output=True,
        text=True,
        env={**os.environ, "PY_EXEC_MAX_OUTPUT": value},
        timeout=60,
    )
    assert r.returncode != 0
    assert "PY_EXEC_MAX_OUTPUT" in r.stderr
    assert value in r.stderr


def test_line_endings_are_normalised_on_every_platform(run):
    """Windows children write CRLF to pipes; the caller should never see a carriage return."""
    out = run("print('a'); print('b')")
    assert chr(13) not in out
    assert "a" + chr(10) + "b" in out
