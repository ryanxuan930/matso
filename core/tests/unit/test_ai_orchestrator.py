"""O11.4b 自主推演編排：gating（不誤啟）、AI issuer participant、per-faction 起 worker。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from _order_fakes import FakeGateway, seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.ai_loop.orchestrator import (
    autonomy_config_key,
    ensure_ai_participant,
    read_system_ai,
    start_ai_workers,
)
from app.config import Settings
from app.models.tables import SessionParticipant, SystemConfiguration
from app.state.hot_state import InMemoryHotState


class _FakeRedis:
    def __init__(self, data: dict[str, str] | None = None) -> None:
        self._d = dict(data or {})

    def get(self, k: str) -> str | None:
        return self._d.get(k)

    def set(self, k: str, v: str) -> None:
        self._d[k] = v

    def delete(self, k: str) -> None:
        self._d.pop(k, None)


def _seed_system_ai(factory: sessionmaker[Session], ai: dict[str, Any]) -> None:
    with factory() as db:
        db.add(
            SystemConfiguration(
                version_name="test",
                sim_tick_rate_ms=1000,
                global_rules={},
                integration_config={"ai": ai},
                updated_at=datetime(2026, 7, 24, 0, 0, 0),
            )
        )
        db.commit()


def _start(world, factory, redis, **kw):  # type: ignore[no-untyped-def]
    return start_ai_workers(
        session_id=world.session_id,
        hot=InMemoryHotState(),
        redis_client=redis,
        db_factory=factory,
        gateway=FakeGateway(),
        should_stop=lambda: True,  # worker 起後第一輪即退出（不打真 LLM）
        settings=Settings(),
        **kw,
    )


# ---- gating（安全：不誤啟）----


def test_no_config_no_workers(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    tasks = _start(world, session_factory, _FakeRedis())  # 無 ai_config
    assert tasks == []


def test_ai_off_no_workers(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _seed_system_ai(session_factory, {"mode": "AI_OFF", "llm_base_url": "http://x"})
    redis = _FakeRedis(
        {autonomy_config_key(world.session_id): json.dumps({"factions": {"BLUE": {}}})}
    )
    assert _start(world, session_factory, redis) == []  # AI_OFF → 不啟動


def test_no_base_url_no_workers(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _seed_system_ai(session_factory, {"mode": "AI_BARE", "llm_base_url": ""})
    redis = _FakeRedis(
        {autonomy_config_key(world.session_id): json.dumps({"factions": {"BLUE": {}}})}
    )
    assert _start(world, session_factory, redis) == []  # 無 base_url → 不啟動


# ---- helpers ----


def test_ensure_ai_participant_idempotent(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        first = ensure_ai_participant(db, world.session_id, "BLUE")
    with session_factory() as db:
        again = ensure_ai_participant(db, world.session_id, "BLUE")
        part = db.get(SessionParticipant, first)
    assert first == again  # 冪等：不重建
    assert part is not None and part.faction == "BLUE"


def test_read_system_ai(session_factory: sessionmaker[Session]) -> None:
    _seed_system_ai(session_factory, {"mode": "AI_FULL", "llm_model": "gemma"})
    with session_factory() as db:
        ai = read_system_ai(db)
    assert ai["mode"] == "AI_FULL" and ai["llm_model"] == "gemma"


# ---- per-faction 起 worker ----


def test_start_workers_one_task_per_faction(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _seed_system_ai(
        session_factory, {"mode": "AI_BARE", "llm_base_url": "http://x", "llm_model": "m"}
    )
    redis = _FakeRedis(
        {
            autonomy_config_key(world.session_id): json.dumps(
                {"factions": {"BLUE": {"mission": "殲敵"}, "RED": {}}, "heartbeat_s": 0.01}
            )
        }
    )

    def _stub_factory(**_kw: Any) -> Any:
        return object()  # should_stop=True → decider 不被呼叫

    async def _run() -> None:
        tasks = _start(world, session_factory, redis, decider_factory=_stub_factory)
        assert len(tasks) == 2  # BLUE + RED 各一 worker
        for t in tasks:
            await t  # should_stop=True → 快速退出
        # 兩陣營的 AI issuer participant 已建
        with session_factory() as db:
            parts = db.query(SessionParticipant).filter_by(session_id=world.session_id).all()
        assert {p.faction for p in parts} >= {"BLUE", "RED"}

    asyncio.run(_run())
