"""Weather module 設定（環境變數 / .env）。"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class WeatherSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MATSO_WEATHER_", extra="ignore")

    # SYNTHETIC 腳本（JSON 關鍵影格）。未設或不存在 → 插件以 DEGRADED 啟動（O5.1）。
    script_path: Path = Path("data/synthetic_weather.json")

    def script_available(self) -> bool:
        return self.script_path.is_file()
