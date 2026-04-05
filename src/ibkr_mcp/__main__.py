"""Entry point: python -m ibkr_mcp"""

from ibkr_mcp.server import mcp


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
