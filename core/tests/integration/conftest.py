"""整合測試共用 fixture（O1.7/r14 dedup）：連 compose 服務，連不上自動 skip。

開發環境位址集中於此（compose：MariaDB 對外 3307 / Redis 6379）。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import redis as redis_lib
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import make_engine, make_session_factory

DEV_DB_URL = "mysql+pymysql://root:matso_dev_root@localhost:3307/matso"
DEV_REDIS_URL = "redis://localhost:6379/0"


@pytest.fixture(scope="session")
def mariadb_engine() -> Iterator[Engine]:
    eng = make_engine(DEV_DB_URL)
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"MariaDB:3307 未就緒（compose 未啟動？）：{exc}")
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(mariadb_engine: Engine) -> sessionmaker[Session]:
    # 覆蓋 core/tests/conftest.py 的 SQLite 版（整合測試走真 DB）
    return make_session_factory(mariadb_engine)


@pytest.fixture(scope="session")
def redis_client() -> Iterator[redis_lib.Redis]:
    client = redis_lib.Redis.from_url(DEV_REDIS_URL, decode_responses=True)
    try:
        client.ping()
    except Exception as exc:
        pytest.skip(f"Redis:6379 未就緒（compose 未啟動？）：{exc}")
    yield client
    client.close()
