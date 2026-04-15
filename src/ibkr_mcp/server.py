"""FastMCP server: lifespan, tool registration, and MCP resources."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import time
from contextlib import asynccontextmanager

from fastmcp import FastMCP, Context

from ibkr_mcp.cache import ContractCache, ResponseCache
from ibkr_mcp.client import IBKRClient
from ibkr_mcp.config import IBKRConfig
from ibkr_mcp.tools.account import ibkr_account_summary, ibkr_positions
from ibkr_mcp.tools.market import ibkr_fx_rate, ibkr_historical_bars, ibkr_quote
from ibkr_mcp.tools.options import ibkr_option_chain
from ibkr_mcp.tools.search import ibkr_contract_search
from ibkr_mcp.tools.status import ibkr_connection_status

logger = logging.getLogger(__name__)


def _kill_orphan_ibkr_mcp() -> None:
    """Kill any existing ibkr_mcp processes that would hold our client ID.

    When Claude Code restarts, the old MCP server process may linger as an
    orphan, holding the IB Gateway client ID slot and blocking the new process.
    """
    my_pid = os.getpid()
    killed = False
    try:
        result = subprocess.run(
            ["pgrep", "-f", r"python.*-m ibkr_mcp$"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().splitlines():
            pid = int(line.strip())
            if pid != my_pid:
                logger.info("Killing orphan ibkr_mcp process (PID %d)", pid)
                os.kill(pid, signal.SIGTERM)
                killed = True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        logger.debug("Orphan cleanup skipped: pgrep unavailable or timed out")
    except (ProcessLookupError, PermissionError, ValueError) as e:
        logger.debug("Orphan cleanup: %s", e)
    if killed:
        time.sleep(1)  # let orphan release client ID slot before we connect


# ── Lifespan ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def ibkr_lifespan(server: FastMCP):
    """Connect to IB Gateway on startup, disconnect on shutdown."""
    _kill_orphan_ibkr_mcp()

    config = IBKRConfig()
    contract_cache = ContractCache(ttl=config.cache_ttl)
    response_cache = ResponseCache(ttl=120)
    client = IBKRClient(config, contract_cache, response_cache)

    launch_task: asyncio.Task[None] | None = None
    try:
        await client.connect()
    except Exception as e:
        logger.warning("Initial connection failed: %s", e)
        # Spawn the launcher as a background task — the launch script can
        # take up to 3 minutes (1Password unlock, 2FA, etc.) and we MUST NOT
        # block the MCP lifespan on it, or Claude Code's handshake times out
        # before the server yields. The reconnect loop will pick up the
        # connection as soon as port 4001 opens.
        launch_task = asyncio.create_task(client._launch_gateway_if_needed())
        client.start_reconnect()

    try:
        yield {"client": client, "config": config}
    finally:
        if launch_task and not launch_task.done():
            launch_task.cancel()
            try:
                await launch_task
            except (asyncio.CancelledError, Exception):
                pass
        await client.disconnect()


# ── Server ─────────────────────────────────────────────────────────────────

mcp = FastMCP(
    "IBKR Gateway",
    instructions=(
        "Read-only access to Interactive Brokers Gateway via the TWS socket API. "
        "Provides market data, positions, account summaries, option chains, and FX rates. "
        "No order placement — all operations are read-only."
    ),
    lifespan=ibkr_lifespan,
)


# ── Tools ──────────────────────────────────────────────────────────────────

mcp.tool()(ibkr_quote)
mcp.tool()(ibkr_historical_bars)
mcp.tool()(ibkr_fx_rate)
mcp.tool()(ibkr_positions)
mcp.tool()(ibkr_account_summary)
mcp.tool()(ibkr_option_chain)
mcp.tool()(ibkr_contract_search)
mcp.tool()(ibkr_connection_status)


# ── Resources ──────────────────────────────────────────────────────────────

@mcp.resource("portfolio://positions")
async def resource_positions(ctx: Context) -> str:
    """Current portfolio positions as context."""
    client = ctx.lifespan_context.get("client")
    if not client or not client.is_connected:
        return json.dumps({"error": "Not connected to IB Gateway"})
    result = await client.get_positions()
    return json.dumps(result, indent=2)


@mcp.resource("account://summary")
async def resource_account_summary(ctx: Context) -> str:
    """Account summary as context."""
    client = ctx.lifespan_context.get("client")
    if not client or not client.is_connected:
        return json.dumps({"error": "Not connected to IB Gateway"})
    result = await client.get_account_summary()
    return json.dumps(result, indent=2)
