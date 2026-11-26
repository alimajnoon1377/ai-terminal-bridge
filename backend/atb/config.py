"""Runtime settings, read from environment variables prefixed with ATB_."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ATB_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    database_url: str = ""
    master_key: str = ""
    """URL-safe base64 of 32 random bytes. Generated into data_dir/master.key when empty."""

    admin_username: str = "admin"
    admin_password: str = ""
    """Only used to create the first administrator when the database has no users."""

    host: str = "127.0.0.1"
    port: int = 8750
    allowed_hosts: str = "127.0.0.1:*,localhost:*,[::1]:*"
    """Host headers accepted by the MCP endpoint (DNS rebinding protection)."""
    allowed_origins: str = ""

    static_dir: str = ""
    session_hours: int = 12
    approval_ttl_seconds: int = 900
    approval_wait_seconds: float = 25.0
    connect_timeout: float = 15.0
    job_poll_seconds: float = 0.4

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{(self.data_dir / 'atb.db').as_posix()}"

    @staticmethod
    def split_list(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
