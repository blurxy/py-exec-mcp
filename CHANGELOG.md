# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-10-05

### Changed

- **Dependency floor is now `mcp>=1.19`** (was 1.2). A tool may return readable text and
  structured content together from 1.19 on, and this release does. Both SDK majors still work.
- Output truncation keeps the **start and the end** of a stream instead of only the start,
  with the gap announced as `--- stdout truncated: 12,043 chars omitted here ---`. The end
  is where tracebacks are.
- `run_python` is async and runs the script in a worker thread. On mcp 1.x a sync tool runs
  on the event loop, which left the whole server unresponsive for the length of the script.

### Added

- **Structured content** on every result: `exit_code`, `stdout`, `stderr`, `timed_out`,
  `timeout_s`, `duration_s`, `interpreter`, `workdir`, `stdout_dropped`, `stderr_dropped`,
  `hint`, `error`.
- **Tool annotations**: destructive, not read-only, open world. Clients that gate on hints
  are no longer told nothing.
- A tool description written for the model that reads it: state does not persist between
  calls, print to see values, stdin is consumed by the code, timeouts return partial output.
- A `--- hint: ... ---` line on `ModuleNotFoundError` naming the interpreter that ran and
  how to point at another one.
- `py.typed`, so type checkers see the annotations the `Typing :: Typed` classifier promised.
- A release workflow (PyPI trusted publishing on `v*` tags), a floor-pinned SDK job in CI,
  mypy in CI, Dependabot for actions, `SECURITY.md`, and a registry `server.json`.

### Fixed

- A timed-out script's **grandchildren are killed too**. Killing only the direct child left
  them running after the call returned, holding the pipes open.
- Memory is bounded while a script runs: a runaway `print` loop used to be buffered in full
  until the timeout, then clipped.
- `PY_EXEC_MAX_OUTPUT=lots` (or `0`, or `-5`) now fails at startup naming the variable instead of with
  `int()`'s message.
- The version lives in one place (`__version__`) and the package metadata is read from it.

## [0.1.0] - 2026-09-16

- First release: one tool, `run_python`, feeding code to the interpreter over stdin.

[Unreleased]: https://github.com/blurxy/py-exec-mcp/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/blurxy/py-exec-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/blurxy/py-exec-mcp/releases/tag/v0.1.0
