"""Terrain module 設定（環境變數 / .env）。

大型地理資產（DTED、OSM、道路網，數 GB）通常放外接硬碟——路徑一律由環境變數注入，
**絕不寫死在程式碼**，且未掛載時系統要能 fallback（見 dted.try_open_default / *_available）。

環境變數（見 modules/terrain/.env.example）：
    MATSO_DTED_PATH        DTED GeoTIFF（O2.1 高程查詢）
    MATSO_OSM_PBF_PATH     OpenStreetMap PBF（O2.2+ terrain_class / 土地利用；尚未使用）
    MATSO_ROAD_GRAPH_PATH  道路網 graphml（O2.4+ 道路型 mobility；尚未使用）
    MATSO_HEX_CACHE_DIR    hex grid 預計算 parquet 快取目錄（O2.2；可放本地讓查詢不依賴外接硬碟）

預設值僅是 repo 內的慣例位置（已被 .gitignore 擋），真檔請用環境變數指向外接硬碟。
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_MODULE_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DATA = _MODULE_ROOT / "data"


class TerrainSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MATSO_", extra="ignore")

    # env: MATSO_DTED_PATH（外接硬碟情境的主要注入點；真檔為 .tif）
    dted_path: Path = _DEFAULT_DATA / "TW_ALL.tif"
    # 尚未使用（O2.2+ / O2.4+）——先納入單一設定面，維持外接硬碟資產路徑的一致注入模式
    osm_pbf_path: Path = _DEFAULT_DATA / "taiwan.osm.pbf"
    road_graph_path: Path = _DEFAULT_DATA / "taiwan_drive.graphml"
    # hex 預計算 parquet 快取目錄（O2.2）。放本地時，查詢不需外接硬碟即可運作（fallback）。
    hex_cache_dir: Path = _DEFAULT_DATA / "hexcache"

    def dted_available(self) -> bool:
        """DTED 真檔是否可讀（外接硬碟已掛載）。供上層決定正常/降級模式，不拋例外。"""
        return self.dted_path.is_file()
