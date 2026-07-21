"""AIInvocationLog 寫入（SPEC_FULL §9.1：所有 AI 請求/回應含 prompt/latency/token 記入 DB）。

鏡 core 的 LedgerWriter：注入 sync `sessionmaker[Session]`。無 factory（純推論測試 / 未接 DB）
時為 no-op（回 None）。ORM 模型 `AIInvocationLog` 在 matso-core，以延遲 import 避免載入期硬耦合。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, sessionmaker


@dataclass(frozen=True)
class InvocationRecord:
    """一次 AI 呼叫的完整記錄（對應 AIInvocationLog 欄位）。"""

    role: str
    adapter: str
    prompt_hash: str
    request: dict[str, Any]
    response: dict[str, Any]
    latency_ms: int
    tokens_in: int
    tokens_out: int
    guardrail_result: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None


class InvocationLogWriter:
    """把 InvocationRecord 落地到 AIInvocationLog。factory=None 時 no-op。"""

    def __init__(self, session_factory: sessionmaker[Session] | None) -> None:
        self._session_factory = session_factory

    def record(self, rec: InvocationRecord) -> str | None:
        if self._session_factory is None:
            return None
        from app.models.tables import AIInvocationLog  # 延遲 import：ai 不在載入期硬綁 core

        with self._session_factory() as db:
            row = AIInvocationLog(
                session_id=rec.session_id,
                role=rec.role,
                adapter=rec.adapter,
                prompt_hash=rec.prompt_hash,
                request=rec.request,
                response=rec.response,
                latency_ms=rec.latency_ms,
                tokens_in=rec.tokens_in,
                tokens_out=rec.tokens_out,
                guardrail_result=rec.guardrail_result,
            )
            db.add(row)
            db.commit()
            return row.id
