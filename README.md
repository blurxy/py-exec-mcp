# py-exec-mcp

**Run Python from an MCP client without a shell mangling your code.**

[![CI](https://github.com/blurxy/py-exec-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/blurxy/py-exec-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/py-exec-mcp)](https://pypi.org/project/py-exec-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/py-exec-mcp)](https://pypi.org/project/py-exec-mcp/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

---

## The problem

Ask an agent to run a one-liner and it reaches for `python -c "..."`. That code passes through
**three parsers** before Python ever sees it — the shell, argv splitting, then Python itself — each
with its own escaping rules.

```bash
python -c "print(f'he said \"hi {name}\"')"     # which layer eats which quote?
```

The failure mode is what makes this worth fixing: **you usually get a wrong answer, not an error.**
A backslash vanishes, a quote closes early, an f-string silently becomes a literal — and the output
looks plausible. So the agent retries with different escaping, or falls back to writing a temp file
and running it, every single time.

## The fix

Code arrives as a JSON string in the MCP tool call and is handed to the interpreter over **stdin**
(`python -`). No shell. No argv. Nothing re-parses it.

Write the code exactly as it would appear in a `.py` file — nested quotes, f-strings, backslashes,
regex, triple-quoted blocks — and it arrives verbatim.

## Install

```bash
uvx py-exec-mcp          # no install
pip install py-exec-mcp  # or the usual way
uvx --from git+https://github.com/blurxy/py-exec-mcp py-exec-mcp   # straight from main
```

Works on **both MCP SDK majors** — 2.x renamed `FastMCP` to `MCPServer`, and the server binds
whichever one your environment has, so you are not forced to pin the SDK to match it. Needs
`mcp>=1.19`.

## Configure

<details open>
<summary><b>Claude Code</b> — <code>.mcp.json</code> in your project root</summary>

```json
{
  "mcpServers": {
    "py-exec": {
      "command": "uvx",
      "args": ["py-exec-mcp"]
    }
  }
}
```

Claude still knows `python -c` by heart. One line in your `CLAUDE.md` retires it:

```markdown
Run Python through the `run_python` MCP tool, never `python -c` or a heredoc.
```
</details>

<details>
<summary><b>Claude Desktop</b> — <code>claude_desktop_config.json</code></summary>

```json
{
  "mcpServers": {
    "py-exec": {
      "command": "uvx",
      "args": ["py-exec-mcp"],
      "env": { "PY_EXEC_CWD": "/absolute/path/to/your/project" }
    }
  }
}
```
</details>

<details>
<summary><b>Any client</b> — stdio transport</summary>

```bash
python -m py_exec_mcp
```
</details>

## The tool

### `run_python(code: str, timeout_s: float = 120)`

Returns stdout, stderr and the exit code as text:

```
42
--- stderr ---
warning: something
--- exit 0 ---
```

and the same facts as **structured content** for programs that would rather not parse it:
`exit_code`, `stdout`, `stderr`, `timed_out`, `timeout_s`, `duration_s`, `interpreter`, `workdir`,
`stdout_dropped`, `stderr_dropped`, `hint`, `error`.

The tool is annotated as destructive, not read-only, and open-world, so clients that gate on hints
hear the truth. Its description tells the model what bites: state does not persist between calls,
only output comes back, stdin is consumed by the code itself, timeouts return partial output.

Six behaviours worth knowing, because each one is a thing that bit somebody:

| behaviour | why |
|---|---|
| **Truncation is announced, and keeps both ends** | a silently clipped result is indistinguishable from a short one, and the end is where the traceback is. You get `--- stdout truncated: 12,043 chars omitted here ---` between the head and the tail |
| **Memory stays bounded while the code runs** | a runaway `print` loop used to be buffered in full until the timeout, then clipped |
| **A timeout still returns output, and kills the whole tree** | the runs that time out are the ones whose partial output matters most, and killing only the direct child left its grandchildren running |
| **The server keeps answering while code runs** | on mcp 1.x a sync tool runs on the event loop; a 2-minute script froze every ping |
| **The workdir is on `PYTHONPATH`** | your project's own packages import without a `sys.path` dance |
| **A missing module names the interpreter** | `ModuleNotFoundError` nearly always means the wrong interpreter ran, and the caller could not see which. The result ends with `--- hint: the interpreter was ...; install the module there, or set PY_EXEC_PYTHON ... ---` |

## Configuration

All optional. Everything works with none of them set.

| variable | default | what it does |
|---|---|---|
| `PY_EXEC_CWD` | process cwd | directory code runs in, and the root added to `PYTHONPATH` |
| `PY_EXEC_PYTHON` | project venv, else `sys.executable` | interpreter to run code with |
| `PY_EXEC_MAX_TIMEOUT` | `600` | upper clamp on `timeout_s` |
| `PY_EXEC_MAX_OUTPUT` | `30000` | per-stream character cap before the middle of the output is cut |

**Interpreter resolution**, in order: `PY_EXEC_PYTHON` → `.venv/Scripts/python.exe` (Windows) or
`.venv/bin/python` (everywhere else) under the working directory → the interpreter running the
server. So in a project with a virtualenv, your dependencies are simply there.

A setting that is not a number fails at startup naming the variable, not three stack frames into
`int()`.

## ⚠️ Security

**This server executes arbitrary Python with the full privileges of the process that launched it.**
That is its entire purpose, and it is not sandboxed.

- Code runs as **your user**, with your filesystem access and your network access.
- The child process **inherits the server's environment**, so any secrets already exported into it
  are readable by executed code. If that matters, launch the server with a scrubbed environment.
- Timeouts bound how long code runs. They bound nothing else.

Give it the same trust you would give a terminal. If you would not paste a script into your shell
and hit enter, do not ask an agent to run it here. For untrusted code, run this inside a container
or a VM — the isolation has to come from the layer underneath, because this server provides none.

Found something that breaks a promise this file makes? See [SECURITY.md](SECURITY.md).

## Development

```bash
git clone https://github.com/blurxy/py-exec-mcp
cd py-exec-mcp
pip install -e ".[dev]"
pytest -q
ruff check . && ruff format --check . && mypy
```

Tests assert on **what a caller reads** — the returned text and structured content, through a real
client session — never on "it did not crash". A test that asserts exit 0 has tested the process
surviving, not the answer being right. CI runs the suite on Linux, macOS and Windows across Python
3.10–3.13, because cross-platform interpreter resolution is the part most likely to break, plus one
job on the newest 1.x SDK and one pinned to the declared floor — the matrix always resolves the
newest SDK, so without those two the 1.x import path and the floor would never be exercised.

**Releasing:** bump `__version__` in `src/py_exec_mcp/__init__.py` and `server.json`, add the
CHANGELOG entry, tag `vX.Y.Z`, push the tag. The release workflow builds, checks that the tag
matches the version, installs the wheel, and publishes through PyPI trusted publishing.

## Prior art

MCP has several Python-execution servers, and most target a different problem: sandboxing
(containers, Pyodide, gVisor), or a persistent REPL/kernel where state survives between calls.

This one is deliberately narrow. **It solves the quoting problem and nothing else** — one tool, no
sandbox, no session state, no notebook. If you need isolation, use a sandboxed server. If you need
variables to persist across calls, use a Jupyter-backed one. If you keep losing an afternoon to
escaping, this is the smaller thing.

## Licence

[Apache-2.0](LICENSE)

<!-- MCP registry ownership marker: keep on its own line -->
mcp-name: io.github.blurxy/py-exec-mcp
