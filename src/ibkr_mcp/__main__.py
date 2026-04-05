"""Entry point: python -m ibkr_mcp"""

# nest_asyncio MUST be applied before any ib_async import.
# ib_async's internal event loop calls conflict with FastMCP's
# anyio-managed asyncio loop without this patch.
import nest_asyncio
nest_asyncio.apply()

from ibkr_mcp.server import mcp  # noqa: E402


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
