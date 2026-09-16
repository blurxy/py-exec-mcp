"""The py-exec MCP server: run Python with zero shell-quoting layers."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

try:  # mcp 2.x renamed FastMCP to MCPServer. Nothing else this server touches moved.
    from mcp.server.mcpserver import MCPServer as _Server
except ModuleNotFoundError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

__all__ = ["build", "resolve_interpreter", "resolve_workdir"]

DEFAULT_TIMEOUT = 120.0
MIN_TIMEOUT = 1.0
MAX_TIMEOUT = float(os.environ.get("PY_EXEC_MAX_TIMEOUT", "600"))
MAX_OUTPUT = int(os.environ.get("PY_EXEC_MAX_OUTPUT", "30000"))


def resolve_workdir() -> Path:
    """Where code runs. PY_EXEC_CWD, else the process working directory.

    Never derived from this file's location: once pip-installs it into site-packages,
    a path relative to __file__ points at the library, not at the user's project.
    """
    return Path(os.environ.get("PY_EXEC_CWD") or Path.cwd()).resolve()


def resolve_interpreter(workdir: Path | None = None) -> Path:
    """PY_EXEC_PYTHON, else the project venv, else the running interpreter.

    The venv lookup checks both layouts because they differ by platform:
    `Scripts/python.exe` on Windows, `bin/python` elsewhere.
    """
    explicit = os.environ.get("PY_EXEC_PYTHON")
    if explicit:
        return Path(explicit)
    wd = workdir or resolve_workdir()
    for rel in ("Scripts/python.exe", "bin/python", "bin/python3"):
        candidate = wd / ".venv" / rel
        if candidate.exists():
            return candidate
    return Path(sys.executable)


def _clip(text: str, label: str) -> str:
    """Truncate loudly. Silent truncation is indistinguishable from a short result."""
    if len(text) <= MAX_OUTPUT:
        return text
    dropped = len(text) - MAX_OUTPUT
    return f"{text[:MAX_OUTPUT]}\n--- {label} truncated: {dropped:,} more chars ---"


def _render(stdout: str, stderr: str, exit_line: str) -> str:
    parts = []
    if stdout:
        parts.append(_clip(stdout, "stdout"))
    if stderr:
        parts.append(f"--- stderr ---\n{_clip(stderr, 'stderr')}")
    parts.append(exit_line)
    return "\n".join(parts)


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
        workdir = resolve_workdir()
        python = resolve_interpreter(workdir)
        timeout = max(MIN_TIMEOUT, min(MAX_TIMEOUT, float(timeout_s)))

        env = dict(os.environ)
        env["PYTHONPATH"] = str(workdir) + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("PYTHONIOENCODING", "utf-8")

        try:
            proc = subprocess.run(
                [str(python), "-"],
                input=code,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(workdir),
                env=env,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            # TimeoutExpired carries whatever was captured before the kill. Returning
            # nothing here throws away the output of exactly the runs that need it most.
            partial_out = exc.stdout or ""
            partial_err = exc.stderr or ""
            if isinstance(partial_out, bytes):
                partial_out = partial_out.decode("utf-8", "replace")
            if isinstance(partial_err, bytes):
                partial_err = partial_err.decode("utf-8", "replace")
            return _render(partial_out, partial_err, f"--- TIMEOUT after {timeout:.0f}s ---")
        except OSError as exc:
            return f"--- could not start interpreter {python}: {exc} ---"

        return _render(proc.stdout or "", proc.stderr or "", f"--- exit {proc.returncode} ---")

    return mcp
