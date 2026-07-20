"""Weather module 設定（環境變數 / .env）。SYNTHETIC（O5.1）與 LIVE（O5.2）模式。"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from weather.cwa import DEFAULT_CWA_URL


class WeatherSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MATSO_WEATHER_", extra="ignore")

    mode: str = "SYNTHETIC"  # SYNTHETIC | LIVE
    # SYNTHETIC：JSON 關鍵影格腳本
    script_path: Path = Path("data/synthetic_weather.json")
    # LIVE：CWA 授權碼（env 注入，絕不寫死）+ 目標格網 cells（JSON h3 清單）+ 拉取間隔
    cwa_api_key: str = ""
    cwa_url: str = DEFAULT_CWA_URL
    live_cells_path: Path = Path("data/live_cells.json")
    fetch_interval_s: float = 600.0  # SPEC §5.1：預設 10 分鐘

    def script_available(self) -> bool:
        return self.script_path.is_file()

    def live_configured(self) -> bool:
        return bool(self.cwa_api_key)
