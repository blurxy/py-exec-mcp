"""Behaviour tests. Each asserts on what a CALLER reads, never on "it did not crash".

A test named "handles X" that asserts exit 0 has tested the process surviving, not the
answer being right. Every assertion here is against the returned string a client acts on.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from py_exec_mcp import server


@pytest.fixture
def run(monkeypatch, tmp_path):
    """Call the tool function directly, with the workdir pinned to a temp dir."""
    monkeypatch.setenv("PY_EXEC_CWD", str(tmp_path))
    monkeypatch.setenv("PY_EXEC_PYTHON", sys.executable)
    tool = server.build()._tool_manager.get_tool("run_python")
    return lambda code, **kw: tool.fn(code, **kw)


# ── the reason this server exists ─────────────────────────────────────────────

NASTY = "\n".join(
    [
        'name = "world"',
        """print(f'he said "hello {name}" and it held')""",
        r'print(r"C:\demo\x\n is not a newline")',
        "print('nested ' + \"quotes \" + '''and triples''')",
    ]
)


def test_nested_quotes_and_fstrings_survive_verbatim(run):
    """THE POINT OF THE PROJECT. Through a shell this mangles; over stdin it must not."""
    out = run(NASTY)
    assert 'he said "hello world" and it held' in out
    assert r"C:\demo\x\n is not a newline" in out
    assert "nested quotes and triples" in out


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


# ── the fixes, each named for the failure it prevents ─────────────────────────


def test_truncation_is_announced_not_silent(run, monkeypatch):
    """A silently clipped result is indistinguishable from a short one."""
    monkeypatch.setattr(server, "MAX_OUTPUT", 200)
    out = run("print('x' * 5000)")
    assert "truncated" in out
    assert "more chars" in out, "the caller must be able to tell output was dropped"


def test_timeout_returns_what_was_produced(run):
    """The runs that time out are the ones whose partial output matters most."""
    code = "import time\nprint('before the hang', flush=True)\ntime.sleep(30)"
    out = run(code, timeout_s=2)
    assert "--- TIMEOUT after 2s ---" in out
    assert "before the hang" in out, "partial output was discarded on timeout"


def test_timeout_is_clamped_at_both_ends(run):
    assert "--- exit 0 ---" in run("print('fast')", timeout_s=0.001)
    assert "--- exit 0 ---" in run("print('fast')", timeout_s=10**9)


def test_a_missing_interpreter_reports_itself(monkeypatch, tmp_path):
    monkeypatch.setenv("PY_EXEC_CWD", str(tmp_path))
    monkeypatch.setenv("PY_EXEC_PYTHON", str(tmp_path / "definitely-not-here"))
    tool = server.build()._tool_manager.get_tool("run_python")
    assert "could not start interpreter" in tool.fn("print(1)")


# ── resolution: the portability half ──────────────────────────────────────────


def test_explicit_interpreter_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("PY_EXEC_PYTHON", "/custom/python")
    assert server.resolve_interpreter(tmp_path) == Path("/custom/python")


@pytest.mark.parametrize("layout", ["Scripts/python.exe", "bin/python", "bin/python3"])
def test_both_venv_layouts_are_found(monkeypatch, tmp_path, layout):
    """Windows puts it in Scripts/, everyone else in bin/. Hardcoding one is the bug."""
    monkeypatch.delenv("PY_EXEC_PYTHON", raising=False)
    target = tmp_path / ".venv" / layout
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("")
    assert server.resolve_interpreter(tmp_path) == target


def test_falls_back_to_the_running_interpreter(monkeypatch, tmp_path):
    monkeypatch.delenv("PY_EXEC_PYTHON", raising=False)
    assert server.resolve_interpreter(tmp_path) == Path(sys.executable)


def test_workdir_is_never_derived_from_the_package_location(monkeypatch, tmp_path):
    """Once pip-installed, a path relative to __file__ points into site-packages."""
    monkeypatch.setenv("PY_EXEC_CWD", str(tmp_path))
    resolved = server.resolve_workdir()
    assert resolved == tmp_path.resolve()
    assert Path(server.__file__).parent not in resolved.parents


def test_code_runs_in_the_workdir_and_can_import_from_it(run, tmp_path):
    (tmp_path / "local_module.py").write_text("VALUE = 'imported-from-workdir'\n")
    out = run("import local_module; print(local_module.VALUE)")
    assert "imported-from-workdir" in out


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
    assert r.stdout.strip() == "0.1.0"
