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

    # 認證（O4.1，SPEC §12）。jwt_secret 絕不寫死於程式——env 注入（JWT_SECRET，無前綴同
    # DATABASE_URL）；未設時用開發預設並於啟動警告（同 CWA/DTED 慣例）。正式部署 MUST 覆寫。
    # 開發預設 ≥32 bytes（HS256 建議長度）；仍不安全，正式部署 MUST 覆寫。
    jwt_secret: str = "dev-insecure-change-me-in-production-0000"
    jwt_algorithm: str = "HS256"
    access_token_ttl_s: int = 900  # 15 分鐘（短效，SPEC §12）
    refresh_token_ttl_s: int = 1209600  # 14 天
    # 前端 COP 的跨來源存取（O4.1）。逗號分隔；env CORS_ORIGINS 覆寫（compose/正式部署設實際來源）。
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def jwt_secret_is_default(self) -> bool:
        """是否仍用不安全的開發預設 secret（供啟動警告）。"""
        return self.jwt_secret == "dev-insecure-change-me-in-production-0000"

    @property
    def sqlalchemy_url(self) -> str:
        """把 Prisma 風格的 mysql:// 轉為 SQLAlchemy + pymysql 的 driver URL。"""
        url = self.database_url
        prefix = "mysql://"
        if url.startswith(prefix):
            return "mysql+pymysql://" + url[len(prefix) :]
        return url
