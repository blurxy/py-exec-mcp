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
```

Works on **both MCP SDK majors** — 2.x renamed `FastMCP` to `MCPServer`, and the server binds
whichever one your environment has, so you are not forced to pin the SDK to match it.

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

### `run_python(code: str, timeout_s: float = 120) -> str`

Returns stdout, stderr and the exit code as text:

```
42
--- stderr ---
warning: something
--- exit 0 ---
```

Four behaviours worth knowing, because each one is a thing that bit somebody:

| behaviour | why |
|---|---|
| **Truncation is announced** | a silently clipped result is indistinguishable from a short one. You get `--- stdout truncated: 12,043 more chars ---` |
| **A timeout still returns output** | the runs that time out are the ones whose partial output matters most |
| **The workdir is on `PYTHONPATH`** | your project's own packages import without a `sys.path` dance |
| **A missing interpreter says so** | rather than failing as an empty result |

## Configuration

All optional. Everything works with none of them set.

| variable | default | what it does |
|---|---|---|
| `PY_EXEC_CWD` | process cwd | directory code runs in, and the root added to `PYTHONPATH` |
| `PY_EXEC_PYTHON` | project venv, else `sys.executable` | interpreter to run code with |
| `PY_EXEC_MAX_TIMEOUT` | `600` | upper clamp on `timeout_s` |
| `PY_EXEC_MAX_OUTPUT` | `30000` | per-stream character cap before truncation |

**Interpreter resolution**, in order: `PY_EXEC_PYTHON` → `.venv/Scripts/python.exe` (Windows) or
`.venv/bin/python` (everywhere else) under the working directory → the interpreter running the
server. So in a project with a virtualenv, your dependencies are simply there.

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

## Development

```bash
git clone https://github.com/blurxy/py-exec-mcp
cd py-exec-mcp
pip install -e ".[dev]"
pytest -q
ruff check . && ruff format --check .
```

Tests assert on **what a caller reads** — the returned string — never on "it did not crash". A test
that asserts exit 0 has tested the process surviving, not the answer being right. CI runs the suite
on Linux, macOS and Windows across Python 3.10–3.13, because cross-platform interpreter resolution
is the part most likely to break, plus one job pinned to `mcp<2` — the matrix always resolves the
newest SDK, so without that job the 1.x import path would never be exercised.

## Prior art

MCP has several Python-execution servers, and most target a different problem: sandboxing
(containers, Pyodide, gVisor), or a persistent REPL/kernel where state survives between calls.

This one is deliberately narrow. **It solves the quoting problem and nothing else** — one tool, no
sandbox, no session state, no notebook. If you need isolation, use a sandboxed server. If you need
variables to persist across calls, use a Jupyter-backed one. If you keep losing an afternoon to
escaping, this is the smaller thing.

## Licence

[Apache-2.0](LICENSE)
