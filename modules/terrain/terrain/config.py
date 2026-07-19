"""Terrain module 設定（環境變數 / .env）。

DTED 真檔（TW_ALL.tiff，數 GB）通常放外接硬碟——路徑一律可由 `MATSO_DTED_PATH`
環境變數指定，預設值僅是 repo 內的慣例位置（modules/terrain/data/，已被 .gitignore 擋）。

範例：
    export MATSO_DTED_PATH="/Volumes/外接硬碟/geodata/TW_ALL.tiff"
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_DTED = Path(__file__).resolve().parents[1] / "data" / "TW_ALL.tiff"


class TerrainSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MATSO_", extra="ignore")

    # env: MATSO_DTED_PATH（外接硬碟情境的主要注入點）
    dted_path: Path = _DEFAULT_DTED
