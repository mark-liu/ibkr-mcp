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
