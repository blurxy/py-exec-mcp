"""Entry point: `python -m py_exec_mcp`, stdio transport."""

from py_exec_mcp.server import build


def main() -> None:
    build().run()


if __name__ == "__main__":
    main()
