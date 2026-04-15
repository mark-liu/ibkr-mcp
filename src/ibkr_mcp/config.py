"""Environment variable configuration."""

import os


class IBKRConfig:
    """Configuration loaded from environment variables."""

    def __init__(self) -> None:
        self.host: str = os.getenv("IB_HOST", "127.0.0.1")
        self.port: int = int(os.getenv("IB_PORT", "4001"))
        self.client_id: int = int(os.getenv("IB_CLIENT_ID", "10"))
        self.market_data_type: int = int(os.getenv("IB_MARKET_DATA_TYPE", "3"))
        self.reconnect_interval: int = int(os.getenv("IB_RECONNECT_INTERVAL", "30"))
        self.cache_ttl: int = int(os.getenv("IB_CACHE_TTL", "3600"))
        self.heartbeat_interval: int = int(os.getenv("IB_HEARTBEAT_INTERVAL", "60"))
        self.gateway_process_name: str = os.getenv(
            "IB_GATEWAY_PROCESS_NAME", "JavaApplicationStub"
        )
        self.gateway_window_name: str = os.getenv(
            "IB_GATEWAY_WINDOW_NAME", "IBKR Gateway"
        )
        self.gateway_restart_script: str = os.getenv(
            "IB_GATEWAY_RESTART_SCRIPT", ""
        )
        # Max failed reconnect attempts before we force a kill+restart of the app
        self.max_reconnect_before_restart: int = int(
            os.getenv("IB_MAX_RECONNECT_BEFORE_RESTART", "2")
        )
