"""Core 執行期設定（環境變數 / .env）。

DATABASE_URL 沿用 Prisma 的 `mysql://` 格式（schema 權威在 db/prisma），
本模組轉為 SQLAlchemy 需要的 `mysql+pymysql://`。
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # 預設指向本機 compose 的 MariaDB（對外 3307，見 ops/compose）
    database_url: str = "mysql://root:matso_dev_root@localhost:3307/matso"
    redis_url: str = "redis://localhost:6379/0"
    # Terrain 插件 gRPC 位址（O2.5）；compose 內為 terrain:50051，本機開發為 localhost:50051
    terrain_grpc_target: str = "localhost:50051"

    @property
    def sqlalchemy_url(self) -> str:
        """把 Prisma 風格的 mysql:// 轉為 SQLAlchemy + pymysql 的 driver URL。"""
        url = self.database_url
        prefix = "mysql://"
        if url.startswith(prefix):
            return "mysql+pymysql://" + url[len(prefix) :]
        return url
