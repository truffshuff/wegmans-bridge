"""Settings, read from environment variables (or a .env file loaded by Docker)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        raise SystemExit(f"Missing required environment variable: {name}")
    return val or ""


@dataclass
class Settings:
    # Norish
    norish_url: str = field(default_factory=lambda: _env("NORISH_URL", "").rstrip("/"))
    norish_api_key: str = field(default_factory=lambda: _env("NORISH_API_KEY", ""))
    norish_store_name: str = field(default_factory=lambda: _env("NORISH_STORE_NAME", "Wegmans"))

    # Wegmans
    store_number: str = field(default_factory=lambda: _env("WEGMANS_STORE_NUMBER", "53"))
    store_key: str = field(default_factory=lambda: _env("WEGMANS_STORE_KEY", "53-BEL-AIR"))
    # Public keys that wegmans.com ships to every browser. Override if they rotate.
    algolia_app_id: str = field(default_factory=lambda: _env("WEGMANS_ALGOLIA_APP_ID", "QGPPR19V8V"))
    algolia_api_key: str = field(
        default_factory=lambda: _env("WEGMANS_ALGOLIA_API_KEY", "9a10b1401634e9a6e55161c3a60c200d")
    )
    apim_key: str = field(
        default_factory=lambda: _env("WEGMANS_APIM_KEY", "b90c717686664b16b5af015ff0a5ef4c")
    )

    # Runtime
    data_dir: str = field(default_factory=lambda: _env("DATA_DIR", "./data"))
    interval_seconds: int = field(default_factory=lambda: int(_env("SYNC_INTERVAL", "120")))
    # Optional: Home Assistant webhook to ping when the Wegmans login needs redoing
    ha_webhook_url: str = field(default_factory=lambda: _env("HA_WEBHOOK_URL", ""))
    # When True, only log what would change
    dry_run: bool = field(default_factory=lambda: _env("DRY_RUN", "false").lower() == "true")

    @property
    def profile_dir(self) -> str:
        return os.path.join(self.data_dir, "browser-profile")

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "bridge.sqlite3")
