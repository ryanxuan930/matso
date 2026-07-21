"""部署安全設定：production fail-fast（C13）+ CORS 萬用字元偵測（C14）。"""

from __future__ import annotations

import pytest

from app.config import Settings

_SAFE_SECRET = "a-sufficiently-long-production-secret-value-000000"


def test_production_fails_fast_on_default_jwt_secret() -> None:
    s = Settings(matso_env="production")  # jwt_secret 仍為開發預設
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        s.ensure_production_safe()


def test_production_fails_fast_on_stub_gateway() -> None:
    s = Settings(matso_env="production", jwt_secret=_SAFE_SECRET, stub_gateway=True)
    with pytest.raises(RuntimeError, match="STUB_GATEWAY"):
        s.ensure_production_safe()


def test_production_fails_fast_on_cors_wildcard() -> None:
    s = Settings(matso_env="production", jwt_secret=_SAFE_SECRET, cors_origins="*")
    assert s.cors_allows_wildcard is True
    with pytest.raises(RuntimeError, match="CORS"):
        s.ensure_production_safe()


def test_production_safe_config_passes() -> None:
    s = Settings(
        matso_env="production",
        jwt_secret=_SAFE_SECRET,
        stub_gateway=False,
        cors_origins="https://cop.example.mil",
    )
    s.ensure_production_safe()  # 不拋


def test_development_never_fails_fast() -> None:
    Settings().ensure_production_safe()  # 開發預設一律放行
