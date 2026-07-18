"""SQLAlchemy engine 與 session factory（同步）。

Core 以 SQLAlchemy 唯讀跟隨 db/prisma/schema.prisma（SPEC_FULL §15.4）；migration 一律走 prisma。
tick loop 為單一寫入者，同步 session 足夠；不引入 async engine 以維持裁決/寫入邏輯的可測試性。
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings


def make_engine(url: str | None = None) -> Engine:
    resolved = url if url is not None else Settings().sqlalchemy_url
    # pool_pre_ping：長時間閒置後自動偵測失效連線並重連
    return create_engine(resolved, pool_pre_ping=True, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@lru_cache(maxsize=1)
def default_session_factory() -> sessionmaker[Session]:
    """行程共用的預設 session factory（連預設 DATABASE_URL）。"""
    return make_session_factory(make_engine())
