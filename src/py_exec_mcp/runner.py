"""The execution core: feed code to an interpreter over stdin, capture bounded output.

Plain Python, no MCP in it, so it can be tested by running real code and reading
the real result.
"""

from __future__ import annotations

import codecs
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO

DEFAULT_TIMEOUT = 120.0
MIN_TIMEOUT = 1.0
MAX_TIMEOUT = float(os.environ.get("PY_EXEC_MAX_TIMEOUT", "600"))
MAX_OUTPUT = int(os.environ.get("PY_EXEC_MAX_OUTPUT", "30000"))

_CHUNK = 65536


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


class BoundedBuffer:
    """Keeps the first and last part of a stream and never more than `limit` chars.

    The head gets 60% and the tail 40%. Both ends matter to a caller: the head is
    what the code printed first, the tail is where a traceback lands. Memory stays
    bounded however much the child writes, and the gap is announced with its size,
    because a silently clipped result is indistinguishable from a short one.
    """

    def __init__(self, limit: int) -> None:
        self._head_cap = max(1, limit * 6 // 10)
        self._tail_cap = max(0, limit - self._head_cap)
        self._head: list[str] = []
        self._head_len = 0
        self._tail = ""
        self.total = 0

    def write(self, chunk: str) -> None:
        self.total += len(chunk)
        if self._head_len < self._head_cap:
            take = chunk[: self._head_cap - self._head_len]
            self._head.append(take)
            self._head_len += len(take)
            chunk = chunk[len(take) :]
            if not chunk:
                return
        if self._tail_cap:
            self._tail = (self._tail + chunk)[-self._tail_cap :]

    @property
    def kept(self) -> int:
        return self._head_len + len(self._tail)

    @property
    def dropped(self) -> int:
        return self.total - self.kept

    def text(self, label: str) -> str:
        head = "".join(self._head)
        if not self.dropped:
            return head + self._tail
        gap = f"--- {label} truncated: {self.dropped:,} chars omitted here ---"
        return f"{head}\n{gap}\n{self._tail}"


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    timeout_s: float
    duration_s: float
    interpreter: str
    workdir: str
    stdout_dropped: int = 0
    stderr_dropped: int = 0
    error: str | None = None


def _isolation() -> dict[str, bool]:
    """Popen options that make the child the root of its own tree so it can be killed whole."""
    return {} if sys.platform == "win32" else {"start_new_session": True}


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Kill the child and everything it spawned.

    Killing only the direct child leaves its grandchildren running after the call
    returns, holding the pipes open and doing whatever they were doing.
    """
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    proc.kill()  # the direct child, in case the tree kill raced with a normal exit


def _pump(stream: IO[bytes], buf: BoundedBuffer) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    while True:
        data = os.read(stream.fileno(), _CHUNK)
        if not data:
            buf.write(decoder.decode(b"", final=True))
            return
        buf.write(decoder.decode(data))


def execute(code: str, timeout_s: float = DEFAULT_TIMEOUT) -> RunResult:
    """Run `code` with the resolved interpreter and return everything a caller needs."""
    workdir = resolve_workdir()
    python = resolve_interpreter(workdir)
    timeout = max(MIN_TIMEOUT, min(MAX_TIMEOUT, float(timeout_s)))

    env = dict(os.environ)
    env["PYTHONPATH"] = str(workdir) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            [str(python), "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(workdir),
            env=env,
            **_isolation(),
        )
    except OSError as exc:
        return RunResult(
            stdout="",
            stderr="",
            exit_code=None,
            timed_out=False,
            timeout_s=timeout,
            duration_s=0.0,
            interpreter=str(python),
            workdir=str(workdir),
            error=f"could not start interpreter {python}: {exc}",
        )

    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    out_buf, err_buf = BoundedBuffer(MAX_OUTPUT), BoundedBuffer(MAX_OUTPUT)
    readers = [
        threading.Thread(target=_pump, args=(proc.stdout, out_buf), daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr, err_buf), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        proc.stdin.write(code.encode("utf-8"))
        proc.stdin.close()
    except OSError:
        pass  # the child exited before reading its input; its exit code says why

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        proc.wait()
    for reader in readers:
        reader.join(timeout=5)

    return RunResult(
        stdout=out_buf.text("stdout"),
        stderr=err_buf.text("stderr"),
        exit_code=None if timed_out else proc.returncode,
        timed_out=timed_out,
        timeout_s=timeout,
        duration_s=round(time.monotonic() - started, 3),
        interpreter=str(python),
        workdir=str(workdir),
        stdout_dropped=out_buf.dropped,
        stderr_dropped=err_buf.dropped,
    )


def render(result: RunResult) -> str:
    """The text a caller reads: stdout, labelled stderr, then one line that says how it ended."""
    if result.error:
        return f"--- {result.error} ---"
    parts = []
    if result.stdout:
        parts.append(result.stdout)
    if result.stderr:
        parts.append(f"--- stderr ---\n{result.stderr}")
    if result.timed_out:
        parts.append(f"--- TIMEOUT after {result.timeout_s:.0f}s ---")
    else:
        parts.append(f"--- exit {result.exit_code} ---")
    return "\n".join(parts)
