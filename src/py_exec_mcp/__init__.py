"""py-exec-mcp — run Python from an MCP client with no shell quoting in the way.

Inline `python -c "..."` sends code through three parsers before it runs: the shell,
argv splitting, then Python. Each has different escaping rules, so f-strings with
quotes, backslashes and here-doc nesting break in ways that produce a WRONG ANSWER
rather than an error. This server feeds code to the interpreter over stdin instead,
so no shell or argv parsing ever touches it.
"""

from py_exec_mcp.server import build, resolve_interpreter, resolve_workdir

__version__ = "0.1.0"
__all__ = ["build", "resolve_interpreter", "resolve_workdir", "__version__"]
