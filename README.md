# ibkr-mcp

Read-only [MCP](https://modelcontextprotocol.io/) server for Interactive Brokers Gateway via the TWS socket API. Connects directly to your running IB Gateway on localhost — no Client Portal REST API, no bundled Java gateway, no 264 MB npm packages.

## What it does

Exposes IB Gateway market data, positions, and account info as MCP tools that any MCP client (Claude Code, Claude Desktop, etc.) can call.

**Read-only by design.** No order placement tools. The connection uses `readonly=True` at the API level — IB Gateway will reject order submissions even if the code is modified.

## Prerequisites

- **IB Gateway** or **Trader Workstation (TWS)** running on localhost (default port 4001)
- **Python 3.11+**
- An active IBKR account (paper or live)

## Installation

```bash
git clone https://github.com/mark-liu/ibkr-mcp.git
cd ibkr-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Configuration

All configuration is via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `IB_HOST` | `127.0.0.1` | Gateway host |
| `IB_PORT` | `4001` | Gateway port (4001=live, 4002=paper) |
| `IB_CLIENT_ID` | `10` | API client ID (must be unique per connection) |
| `IB_MARKET_DATA_TYPE` | `3` | 1=live, 2=frozen, 3=delayed, 4=frozen-delayed |
| `IB_RECONNECT_INTERVAL` | `30` | Seconds between reconnect attempts |
| `IB_CACHE_TTL` | `3600` | Contract cache TTL in seconds |

## Claude Code Integration

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "ibkr": {
      "command": "/path/to/ibkr-mcp/.venv/bin/python",
      "args": ["-m", "ibkr_mcp"],
      "env": {
        "IB_PORT": "4001",
        "IB_CLIENT_ID": "10"
      }
    }
  }
}
```

Then in Claude Code, tools like `ibkr_quote`, `ibkr_positions`, `ibkr_historical_bars` become available automatically.

## Available Tools

| Tool | Description | Key Parameters |
|------|-------------|----------------|
| `ibkr_quote` | Current price quotes | `symbols` (comma/space separated, max 20) |
| `ibkr_historical_bars` | OHLCV historical bars | `symbol`, `duration` ("1 M"), `bar_size` ("1 day") |
| `ibkr_positions` | Portfolio positions with P&L | — |
| `ibkr_account_summary` | NLV, cash, margin, buying power | — |
| `ibkr_option_chain` | Available expirations and strikes | `symbol`, `exchange` (optional) |
| `ibkr_contract_search` | Fuzzy search for contracts | `pattern` |
| `ibkr_fx_rate` | Live FX rate | `pair` ("EURUSD", "AUD/USD") |
| `ibkr_connection_status` | Gateway health check | — |

## MCP Resources

| URI | Description |
|-----|-------------|
| `portfolio://positions` | Current positions as context |
| `account://summary` | Account summary as context |

## Design Decisions

- **TWS socket API, not Client Portal REST.** Direct connection to IB Gateway on port 4001 via `ib_async`. Sub-millisecond local latency, streaming-capable, full options support. No HTTP indirection through a Java gateway.
- **Persistent connection with background reconnect.** If IB Gateway restarts, the server automatically reconnects without manual intervention.
- **Contract caching.** Qualified contracts (with populated `conId`) are cached for 1 hour, eliminating redundant API round-trips.
- **Market hours detection.** Uses `exchange_calendars` (NYSE) to automatically switch between live (type 1) and delayed (type 3) market data.
- **NaN handling.** IB returns `float('nan')` for missing data. Every numeric field is cleaned to `None` before JSON serialization.
- **Rate limiting.** Token bucket limiters respect IB's API limits: 45 req/s for market data, 1 req/s for historical data.
- **Graceful degradation.** Response cache stores last-known-good data, so tools return stale results (flagged) instead of errors during brief disconnects.

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

Tests run without a live IB Gateway — all IB interactions are mocked.

## Project Structure

```
src/ibkr_mcp/
    __init__.py
    __main__.py       # Entry point (nest_asyncio + mcp.run)
    server.py         # FastMCP server, lifespan, tool registration, resources
    client.py         # IBKRClient: connection, caching, all data methods
    config.py         # Environment variable configuration
    cache.py          # Contract cache + response cache
    models.py         # Pydantic input validation
    utils.py          # NaN handling, rate limiter, retry, formatting
    tools/
        market.py     # ibkr_quote, ibkr_historical_bars, ibkr_fx_rate
        account.py    # ibkr_positions, ibkr_account_summary
        options.py    # ibkr_option_chain
        search.py     # ibkr_contract_search
        status.py     # ibkr_connection_status
```

## Acknowledgments

This project was built after evaluating six existing IBKR MCP servers. While none were suitable as-is (wrong API, security issues, proprietary licenses, abandoned), each contributed patterns and lessons:

- **[xiao81/IBKR-MCP-Server](https://github.com/xiao81/IBKR-MCP-Server)** (Apache-2.0) — FastMCP lifespan pattern with typed context, MCP resources for portfolio/account data
- **[ArjunDivecha/ibkr-mcp-server](https://github.com/ArjunDivecha/ibkr-mcp-server)** (MIT) — Rate limiting and retry decorator patterns, symbol validation approach, exception hierarchy design
- **[omdv/ibkr-mcp-server](https://github.com/omdv/ibkr-mcp-server)** — Market hours detection via `exchange_calendars`, contract caching concept, market data type switching
- **[jeffbai996/ibkr-terminal](https://github.com/jeffbai996/ibkr-terminal)** — Background reconnect loop concept, cached degradation pattern, NaN handling throughout, subscription cleanup patterns
- **[code-rabi/interactive-brokers-mcp](https://github.com/code-rabi/interactive-brokers-mcp)** (MIT) — Tool definition and registration patterns, read-only mode enforcement approach
- **[rcontesti/IB_MCP](https://github.com/rcontesti/IB_MCP)** (MIT) — Endpoint categorization and tool description patterns

No code was copied from any of these projects. All implementations are original.

## License

MIT
