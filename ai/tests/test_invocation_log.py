"""AIInvocationLog 寫入 + RoleManager 整合（O6.1）。SQLite，不需 compose。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base
from app.models.tables import AIInvocationLog
from matso_ai.inference.client import LLMResponse
from matso_ai.inference.invocation_log import InvocationLogWriter, InvocationRecord
from matso_ai.inference.role_manager import AIRequest, RoleManager
from matso_ai.roles import Role


@pytest.fixture()
def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


class EchoClient:
    def complete(self, messages, *, model, adapter):  # type: ignore[no-untyped-def]
        return LLMResponse("撤退", tokens_in=11, tokens_out=3, model=model, adapter=adapter)


def test_writer_persists_record(session_factory: sessionmaker[Session]) -> None:
    writer = InvocationLogWriter(session_factory)
    row_id = writer.record(
        InvocationRecord(
            role="OPFOR_COMMANDER",
            adapter="opfor-v1",
            prompt_hash="abc",
            request={"messages": []},
            response={"text": "撤退"},
            latency_ms=42,
            tokens_in=11,
            tokens_out=3,
            guardrail_result={"status": "not_evaluated"},
            session_id="s1",
        )
    )
    assert row_id is not None
    with session_factory() as db:
        row = db.get(AIInvocationLog, row_id)
        assert row is not None
        assert (row.role, row.adapter, row.latency_ms) == ("OPFOR_COMMANDER", "opfor-v1", 42)
        assert row.session_id == "s1"


def test_writer_noop_without_factory() -> None:
    assert (
        InvocationLogWriter(None).record(
            InvocationRecord(
                role="AAR_ANALYST",
                adapter="aar-v1",
                prompt_hash="x",
                request={},
                response={},
                latency_ms=1,
                tokens_in=0,
                tokens_out=0,
            )
        )
        is None
    )


def test_role_manager_logs_each_invocation(session_factory: sessionmaker[Session]) -> None:
    mgr = RoleManager(
        EchoClient(),
        log_writer=InvocationLogWriter(session_factory),
        clock=iter([0.0, 0.01]).__next__,
        model="local-gemma",
    )
    res = mgr.invoke(AIRequest(Role.OPFOR_COMMANDER, "敵在 H-45", session_id="s9"))
    assert res.log_id is not None
    with session_factory() as db:
        row = db.get(AIInvocationLog, res.log_id)
        assert row is not None
        assert row.role == "OPFOR_COMMANDER"
        assert row.tokens_in == 11
        assert row.request["model"] == "local-gemma"
        assert row.request["mode"] == "AI_OFF"  # §9.0：log 記當時模式
        assert row.guardrail_result == {"status": "not_evaluated"}
