"""Tests for IBKRClient."""

import pytest

from tests.conftest import (
    FakeContext,
    MockAccountValue,
    MockBar,
    MockContract,
    MockContractDescription,
    MockOptionChain,
    MockPortfolioItem,
    MockPosition,
    MockTicker,
    make_mock_ib,
)


class TestConnectionStatus:
    @pytest.mark.asyncio
    async def test_connected(self, client):
        status = await client.get_connection_status()
        assert status["connected"] is True
        assert status["accounts"] == ["U1234567"]
        assert status["client_id"] == 10

    @pytest.mark.asyncio
    async def test_disconnected(self, config, contract_cache, response_cache):
        from ibkr_mcp.client import IBKRClient
        c = IBKRClient(config, contract_cache, response_cache)
        c._ib = make_mock_ib(connected=False)
        status = await c.get_connection_status()
        assert status["connected"] is False
        assert status["accounts"] == []


class TestGetQuote:
    @pytest.mark.asyncio
    async def test_single_symbol(self, client):
        result = await client.get_quote(["AAPL"])
        assert len(result) == 1
        assert result[0]["last"] == 150.00
        assert result[0]["change"] == 2.0

    @pytest.mark.asyncio
    async def test_nan_handling(self, client):
        nan_ticker = MockTicker(bid=float("nan"), ask=float("nan"), last=float("nan"))
        client._ib.reqTickersAsync.return_value = [nan_ticker]
        result = await client.get_quote(["AAPL"])
        assert result[0]["bid"] is None
        assert result[0]["last"] is None


class TestGetHistoricalBars:
    @pytest.mark.asyncio
    async def test_returns_bars(self, client):
        result = await client.get_historical_bars("AAPL")
        assert len(result) == 2
        assert result[0]["close"] == 150.00
        assert result[1]["date"] == "2026-04-02"


class TestGetPositions:
    @pytest.mark.asyncio
    async def test_returns_positions(self, client):
        result = await client.get_positions()
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"
        assert result[0]["quantity"] == 100.0
        assert result[0]["unrealized_pnl"] == 2000.00

    @pytest.mark.asyncio
    async def test_empty_portfolio(self, client):
        client._ib.positions.return_value = []
        client._ib.portfolio.return_value = []
        result = await client.get_positions()
        assert result == []


class TestGetAccountSummary:
    @pytest.mark.asyncio
    async def test_extracts_tags(self, client):
        result = await client.get_account_summary()
        assert "NetLiquidation_USD" in result
        assert result["NetLiquidation_USD"] == 50000.00
        assert "accounts" in result


class TestGetOptionChain:
    @pytest.mark.asyncio
    async def test_returns_chain(self, client):
        result = await client.get_option_chain("AAPL")
        assert result["symbol"] == "AAPL"
        assert len(result["chains"]) == 1
        chain = result["chains"][0]
        assert "20260618" in chain["expirations"]
        assert 150.0 in chain["strikes"]


class TestSearchContracts:
    @pytest.mark.asyncio
    async def test_search(self, client):
        result = await client.search_contracts("Apple")
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"


class TestReconnect:
    @pytest.mark.asyncio
    async def test_reconnect_creates_fresh_ib_instance(self, config, contract_cache, response_cache):
        """After disconnect, reconnect loop should create a new IB() instance
        to avoid stale internal state from the dead connection."""
        from unittest.mock import patch, MagicMock, AsyncMock
        from ibkr_mcp.client import IBKRClient

        c = IBKRClient(config, contract_cache, response_cache)
        old_ib = c._ib

        # Simulate a disconnect then one reconnect iteration
        c._reconnecting = True

        # Patch IB constructor to track fresh instance creation
        fresh_ib = make_mock_ib(connected=True)
        with patch("ibkr_mcp.client.IB", return_value=fresh_ib):
            # Patch sleep to not actually wait
            with patch("asyncio.sleep", new_callable=AsyncMock):
                # Run one iteration: the loop checks is_connected, sleeps,
                # disconnects old, creates new IB, connects, then checks again
                # We need is_connected to return False first, then True after connect
                call_count = 0

                def connected_side_effect():
                    nonlocal call_count
                    call_count += 1
                    # First two calls (loop condition + _ensure_market_data_type): False
                    # After connect succeeds: True
                    return call_count > 2

                fresh_ib.isConnected.side_effect = connected_side_effect

                await c._reconnect_loop()

        # Verify: old IB was replaced with fresh instance
        assert c._ib is not old_ib
        assert c._ib is fresh_ib
        # Verify: disconnect event handler re-attached
        fresh_ib.disconnectedEvent.__iadd__.assert_called()


class TestGatewaySupervision:
    """Tests for stuck-UI detection and kill+restart of the Gateway app."""

    def _make_client(self, config, contract_cache, response_cache):
        from ibkr_mcp.client import IBKRClient
        return IBKRClient(config, contract_cache, response_cache)

    def test_process_alive_true(self, config, contract_cache, response_cache):
        from unittest.mock import patch
        c = self._make_client(config, contract_cache, response_cache)
        fake = type("R", (), {"returncode": 0, "stdout": "12345\n"})()
        with patch("ibkr_mcp.client.subprocess.run", return_value=fake):
            assert c._is_gateway_process_alive() is True

    def test_process_alive_false(self, config, contract_cache, response_cache):
        from unittest.mock import patch
        c = self._make_client(config, contract_cache, response_cache)
        fake = type("R", (), {"returncode": 1, "stdout": ""})()
        with patch("ibkr_mcp.client.subprocess.run", return_value=fake):
            assert c._is_gateway_process_alive() is False

    def test_detect_stuck_ui_no_process(self, config, contract_cache, response_cache):
        from unittest.mock import patch
        c = self._make_client(config, contract_cache, response_cache)
        with patch.object(c, "_is_gateway_process_alive", return_value=False):
            # Should short-circuit without calling osascript at all.
            with patch("ibkr_mcp.client.subprocess.run") as run:
                assert c._detect_stuck_ui() is False
                run.assert_not_called()

    def test_detect_stuck_ui_dialog_present(self, config, contract_cache, response_cache):
        from unittest.mock import patch
        c = self._make_client(config, contract_cache, response_cache)
        osa = type("R", (), {"stdout": "dialog:Reconnect to server\n", "stderr": ""})()
        with patch.object(c, "_is_gateway_process_alive", return_value=True), \
             patch("ibkr_mcp.client.shutil.which", return_value="/usr/bin/osascript"), \
             patch("ibkr_mcp.client.subprocess.run", return_value=osa):
            assert c._detect_stuck_ui() is True

    @pytest.mark.parametrize("dialog_title", [
        "Reconnect to server",
        "Connection Lost",
        "Re-login is required",    # observed 2026-04-18 after long idle
        "Attempt 15: Authenticating...",  # Gateway's own retry loop
        "CONNECTION LOST",          # case-insensitive
    ])
    def test_detect_stuck_ui_known_titles(
        self, config, contract_cache, response_cache, dialog_title
    ):
        """Each observed stuck-dialog title must be caught by the keyword list."""
        from ibkr_mcp.client import IBKRClient
        lowered = dialog_title.lower()
        assert any(kw in lowered for kw in IBKRClient._STUCK_DIALOG_KEYWORDS), (
            f"{dialog_title!r} matches none of {IBKRClient._STUCK_DIALOG_KEYWORDS}"
        )

    @pytest.mark.parametrize("benign_title", [
        "IBKR Gateway",                    # main window
        "Second Factor Authentication",    # 2FA dialog, must NOT match
        "File Save",                       # generic dialogs
    ])
    def test_detect_stuck_ui_benign_titles(self, benign_title):
        """Benign dialog titles must NOT hit any keyword (esp. 2FA vs 'authenticating')."""
        from ibkr_mcp.client import IBKRClient
        lowered = benign_title.lower()
        assert not any(kw in lowered for kw in IBKRClient._STUCK_DIALOG_KEYWORDS), (
            f"{benign_title!r} unexpectedly matches a stuck-UI keyword"
        )

    def test_detect_stuck_ui_healthy(self, config, contract_cache, response_cache):
        from unittest.mock import patch
        c = self._make_client(config, contract_cache, response_cache)
        osa = type("R", (), {"stdout": "ok\n", "stderr": ""})()
        with patch.object(c, "_is_gateway_process_alive", return_value=True), \
             patch("ibkr_mcp.client.shutil.which", return_value="/usr/bin/osascript"), \
             patch("ibkr_mcp.client.subprocess.run", return_value=osa):
            assert c._detect_stuck_ui() is False

    def test_detect_stuck_ui_osascript_timeout(self, config, contract_cache, response_cache):
        import subprocess as sp
        from unittest.mock import patch
        c = self._make_client(config, contract_cache, response_cache)
        with patch.object(c, "_is_gateway_process_alive", return_value=True), \
             patch("ibkr_mcp.client.shutil.which", return_value="/usr/bin/osascript"), \
             patch("ibkr_mcp.client.subprocess.run",
                   side_effect=sp.TimeoutExpired(cmd="osascript", timeout=5)):
            assert c._detect_stuck_ui() is False

    @pytest.mark.asyncio
    async def test_restart_gateway_missing_script(self, config, contract_cache, response_cache, tmp_path):
        from unittest.mock import patch
        c = self._make_client(config, contract_cache, response_cache)
        c._config.gateway_restart_script = str(tmp_path / "does-not-exist.sh")
        with patch.object(c, "_kill_gateway_process"), \
             patch.object(c, "_is_gateway_process_alive", return_value=False), \
             patch.object(c, "start_reconnect") as start_rc:
            ok = await c._restart_gateway(reason="test")
        assert ok is False
        assert "Restart script missing" in (c.last_error or "")
        start_rc.assert_called_once()

    @pytest.mark.asyncio
    async def test_restart_gateway_success(self, config, contract_cache, response_cache, tmp_path):
        from unittest.mock import patch, AsyncMock, MagicMock
        c = self._make_client(config, contract_cache, response_cache)

        script = tmp_path / "fake-start.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        script.chmod(0o755)
        c._config.gateway_restart_script = str(script)

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"", b""))

        fresh_ib = make_mock_ib(connected=True)

        with patch.object(c, "_kill_gateway_process"), \
             patch.object(c, "_is_gateway_process_alive", return_value=False), \
             patch("ibkr_mcp.client.asyncio.create_subprocess_exec",
                   new=AsyncMock(return_value=fake_proc)), \
             patch("ibkr_mcp.client.IB", return_value=fresh_ib):
            ok = await c._restart_gateway(reason="test")

        assert ok is True
        assert c._ib is fresh_ib
        fresh_ib.connectAsync.assert_called_once()

    @pytest.mark.asyncio
    async def test_restart_gateway_script_timeout(self, config, contract_cache, response_cache, tmp_path):
        import asyncio as _asyncio
        from unittest.mock import patch, AsyncMock, MagicMock
        c = self._make_client(config, contract_cache, response_cache)

        script = tmp_path / "fake-start.sh"
        script.write_text("#!/bin/bash\nsleep 999\n")
        script.chmod(0o755)
        c._config.gateway_restart_script = str(script)

        fake_proc = MagicMock()
        fake_proc.communicate = AsyncMock(side_effect=_asyncio.TimeoutError())
        fake_proc.kill = MagicMock()

        with patch.object(c, "_kill_gateway_process"), \
             patch.object(c, "_is_gateway_process_alive", return_value=False), \
             patch("ibkr_mcp.client.asyncio.create_subprocess_exec",
                   new=AsyncMock(return_value=fake_proc)), \
             patch("ibkr_mcp.client.asyncio.wait_for",
                   new=AsyncMock(side_effect=_asyncio.TimeoutError())), \
             patch.object(c, "start_reconnect") as start_rc:
            ok = await c._restart_gateway(reason="test")

        assert ok is False
        fake_proc.kill.assert_called_once()
        assert "timed out" in (c.last_error or "")
        start_rc.assert_called_once()

    @pytest.mark.asyncio
    async def test_heartbeat_triggers_restart_when_process_alive(
        self, config, contract_cache, response_cache
    ):
        """Heartbeat hang + live process → kill+restart path, not reconnect loop."""
        from unittest.mock import patch, AsyncMock
        c = self._make_client(config, contract_cache, response_cache)
        c._ib = make_mock_ib(connected=True)
        c._ib.reqCurrentTimeAsync = AsyncMock(side_effect=TimeoutError("hang"))

        with patch.object(c, "_detect_stuck_ui", return_value=False), \
             patch.object(c, "_is_gateway_process_alive", return_value=True), \
             patch.object(c, "_restart_gateway", new=AsyncMock(return_value=True)) as rg, \
             patch("ibkr_mcp.client.asyncio.sleep", new=AsyncMock()):
            await c._heartbeat_loop()

        rg.assert_called_once()
        assert "heartbeat hang" in rg.call_args.kwargs.get("reason", "")

    @pytest.mark.asyncio
    async def test_heartbeat_triggers_restart_on_stuck_ui(
        self, config, contract_cache, response_cache
    ):
        """UI probe fires even when the API socket still looks healthy."""
        from unittest.mock import patch, AsyncMock
        c = self._make_client(config, contract_cache, response_cache)
        c._ib = make_mock_ib(connected=True)

        with patch.object(c, "_detect_stuck_ui", return_value=True), \
             patch.object(c, "_restart_gateway", new=AsyncMock(return_value=True)) as rg, \
             patch("ibkr_mcp.client.asyncio.sleep", new=AsyncMock()):
            await c._heartbeat_loop()

        rg.assert_called_once()
        assert rg.call_args.kwargs.get("reason") == "stuck UI dialog"
        # Heartbeat must NOT have been issued — UI probe short-circuits first.
        c._ib.reqCurrentTimeAsync.assert_not_called()


class TestColdStartPatience:
    """Before the first-ever successful connection, never kill the Gateway —
    the user is probably at the login screen."""

    def _make_client(self, config, contract_cache, response_cache):
        from ibkr_mcp.client import IBKRClient
        return IBKRClient(config, contract_cache, response_cache)

    @pytest.mark.asyncio
    async def test_cold_start_reconnect_never_restarts_with_live_process(
        self, config, contract_cache, response_cache
    ):
        """User is at the login screen (process alive, port closed). The
        reconnect loop must not escalate to kill+restart no matter how many
        iterations fail."""
        from unittest.mock import patch, AsyncMock, MagicMock
        c = self._make_client(config, contract_cache, response_cache)
        assert c._has_ever_connected is False

        c._reconnecting = True
        # Every connect attempt fails — user hasn't logged in yet.
        failing_ib = make_mock_ib(connected=False)
        failing_ib.connectAsync = AsyncMock(side_effect=ConnectionRefusedError("nope"))

        iterations = {"n": 0}

        async def sleep_side_effect(*args, **kwargs):
            iterations["n"] += 1
            if iterations["n"] >= 5:
                c._reconnecting = False

        with patch("ibkr_mcp.client.IB", return_value=failing_ib), \
             patch("ibkr_mcp.client.asyncio.sleep", side_effect=sleep_side_effect), \
             patch.object(c, "_is_gateway_process_alive", return_value=True), \
             patch.object(c, "_restart_gateway", new=AsyncMock(return_value=True)) as rg:
            await c._reconnect_loop()

        rg.assert_not_called()
        assert c._reconnect_failures >= 4
        assert c._last_error == "Waiting for login at IB Gateway"

    @pytest.mark.asyncio
    async def test_cold_start_reports_process_missing(
        self, config, contract_cache, response_cache
    ):
        """Gateway process isn't running at all — surface that in last_error
        without escalating."""
        from unittest.mock import patch, AsyncMock
        c = self._make_client(config, contract_cache, response_cache)
        c._reconnecting = True

        failing_ib = make_mock_ib(connected=False)
        failing_ib.connectAsync = AsyncMock(side_effect=ConnectionRefusedError("nope"))

        iterations = {"n": 0}

        async def sleep_side_effect(*args, **kwargs):
            iterations["n"] += 1
            if iterations["n"] >= 3:
                c._reconnecting = False

        with patch("ibkr_mcp.client.IB", return_value=failing_ib), \
             patch("ibkr_mcp.client.asyncio.sleep", side_effect=sleep_side_effect), \
             patch.object(c, "_is_gateway_process_alive", return_value=False), \
             patch.object(c, "_restart_gateway", new=AsyncMock(return_value=True)) as rg:
            await c._reconnect_loop()

        rg.assert_not_called()
        assert c._last_error == "IB Gateway not running"

    @pytest.mark.asyncio
    async def test_post_login_reconnect_escalates(
        self, config, contract_cache, response_cache
    ):
        """Once we've been connected, a subsequent drop should escalate to
        kill+restart after max_reconnect_before_restart failures."""
        from unittest.mock import patch, AsyncMock
        c = self._make_client(config, contract_cache, response_cache)
        c._has_ever_connected = True  # we were logged in before
        c._reconnecting = True

        failing_ib = make_mock_ib(connected=False)
        failing_ib.connectAsync = AsyncMock(side_effect=ConnectionRefusedError("nope"))

        restart_calls = {"reasons": []}

        async def stop_after_restart(*, reason):
            restart_calls["reasons"].append(reason)
            c._reconnecting = False
            return True

        with patch("ibkr_mcp.client.IB", return_value=failing_ib), \
             patch("ibkr_mcp.client.asyncio.sleep", new=AsyncMock()), \
             patch.object(c, "_is_gateway_process_alive", return_value=False), \
             patch.object(c, "_restart_gateway", new=AsyncMock(side_effect=stop_after_restart)) as rg:
            await c._reconnect_loop()

        rg.assert_called_once()
        # Should escalate only after hitting the failure threshold
        assert f"{c._config.max_reconnect_before_restart} failed reconnects" in restart_calls["reasons"][0]

    @pytest.mark.asyncio
    async def test_connect_sets_has_ever_connected(
        self, config, contract_cache, response_cache
    ):
        from unittest.mock import patch, AsyncMock
        c = self._make_client(config, contract_cache, response_cache)
        c._ib = make_mock_ib(connected=True)
        assert c._has_ever_connected is False

        with patch("ibkr_mcp.client._is_market_open", return_value=False), \
             patch.object(c, "_start_heartbeat", new=AsyncMock()):
            await c.connect()

        assert c._has_ever_connected is True


class TestLaunchGatewayIfNeeded:
    """Cold-start auto-launch helper called from the MCP lifespan."""

    def _make_client(self, config, contract_cache, response_cache):
        from ibkr_mcp.client import IBKRClient
        return IBKRClient(config, contract_cache, response_cache)

    @pytest.mark.asyncio
    async def test_noop_when_process_alive(
        self, config, contract_cache, response_cache
    ):
        from unittest.mock import patch, AsyncMock
        c = self._make_client(config, contract_cache, response_cache)
        with patch.object(c, "_is_gateway_process_alive", return_value=True), \
             patch("ibkr_mcp.client.asyncio.create_subprocess_exec",
                   new=AsyncMock()) as spawn:
            await c._launch_gateway_if_needed()
        spawn.assert_not_called()
        assert c._last_error == "Waiting for login at IB Gateway"

    @pytest.mark.asyncio
    async def test_noop_when_no_script_configured(
        self, config, contract_cache, response_cache
    ):
        from unittest.mock import patch, AsyncMock
        c = self._make_client(config, contract_cache, response_cache)
        c._config.gateway_restart_script = ""
        with patch.object(c, "_is_gateway_process_alive", return_value=False), \
             patch("ibkr_mcp.client.asyncio.create_subprocess_exec",
                   new=AsyncMock()) as spawn:
            await c._launch_gateway_if_needed()
        spawn.assert_not_called()
        assert "no launch script" in (c._last_error or "")

    @pytest.mark.asyncio
    async def test_runs_script_when_process_dead(
        self, config, contract_cache, response_cache, tmp_path
    ):
        from unittest.mock import patch, AsyncMock, MagicMock
        c = self._make_client(config, contract_cache, response_cache)

        script = tmp_path / "fake-start.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        script.chmod(0o755)
        c._config.gateway_restart_script = str(script)

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch.object(c, "_is_gateway_process_alive", return_value=False), \
             patch("ibkr_mcp.client.asyncio.create_subprocess_exec",
                   new=AsyncMock(return_value=fake_proc)) as spawn:
            await c._launch_gateway_if_needed()

        spawn.assert_called_once()
        assert c._last_error == "Waiting for login at IB Gateway"


class TestContractCaching:
    @pytest.mark.asyncio
    async def test_cache_hit(self, client):
        # First call qualifies
        await client.get_quote(["AAPL"])
        call_count_1 = client._ib.qualifyContractsAsync.call_count

        # Second call should use cache
        await client.get_quote(["AAPL"])
        call_count_2 = client._ib.qualifyContractsAsync.call_count

        assert call_count_2 == call_count_1  # no additional qualify call
