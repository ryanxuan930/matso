"""Core 執行期設定（環境變數 / .env）。

DATABASE_URL 沿用 Prisma 的 `mysql://` 格式（schema 權威在 db/prisma），
本模組轉為 SQLAlchemy 需要的 `mysql+pymysql://`。
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # 部署環境（env MATSO_ENV）。production 時對不安全設定 fail-fast（ensure_production_safe）。
    matso_env: str = "development"

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
    # E2E/開發用：以許可式 stub 取代真 terrain 物理預檢（env STUB_GATEWAY=1）。正式部署絕不設。
    stub_gateway: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def cors_allows_wildcard(self) -> bool:
        """CORS 是否含萬用字元 `*`（與 allow_credentials=True 不相容，屬誤設，CODE_REVIEW C14）。"""
        return "*" in self.cors_origin_list

    @property
    def is_production(self) -> bool:
        return self.matso_env.lower() == "production"

    @property
    def jwt_secret_is_default(self) -> bool:
        """是否仍用不安全的開發預設 secret（供啟動警告）。"""
        return self.jwt_secret == "dev-insecure-change-me-in-production-0000"

    def ensure_production_safe(self) -> None:
        """正式部署（MATSO_ENV=production）對不安全設定 fail-fast（CODE_REVIEW C13）。

        紅線 3「護欄不可 bypass」的部署面延伸：預設 JWT secret / STUB_GATEWAY / CORS 萬用字元
        在生產一律拒絕啟動。
        """
        if not self.is_production:
            return
        problems: list[str] = []
        if self.jwt_secret_is_default:
            problems.append("JWT_SECRET 仍為開發預設（可預測金鑰）")
        if self.stub_gateway:
            problems.append("STUB_GATEWAY=1（許可式物理預檢繞過）")
        if self.cors_allows_wildcard:
            problems.append("CORS_ORIGINS 含 '*'（與 credentials 不相容）")
        if problems:
            raise RuntimeError("正式部署設定不安全，拒絕啟動：" + "；".join(problems))

    @property
    def sqlalchemy_url(self) -> str:
        """把 Prisma 風格的 mysql:// 轉為 SQLAlchemy + pymysql 的 driver URL。"""
        url = self.database_url
        prefix = "mysql://"
        if url.startswith(prefix):
            return "mysql+pymysql://" + url[len(prefix) :]
        return url
