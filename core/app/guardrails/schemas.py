"""Guardrail 型別與共用件（SPEC_FULL §10）。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.state.ledger import LedgerEvent


@dataclass(frozen=True, slots=True)
class GuardrailFinding:
    """單一護欄的結果。"""

    check: str  # "G1".."G6"
    blocked: bool  # True＝該檢查判定需干預
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GuardrailOutcome:
    """Gateway 整體結果。"""

    accepted: bool  # False → 走 doctrine fallback（G1 耗盡 / G4 硬阻擋）
    sanitized: dict[str, Any] | None  # 清洗後的 AI 輸出（G3 剔除不可行 order、G5 清引用）
    findings: list[GuardrailFinding]
    escalate_white_cell: bool = False  # G4/G6 硬阻擋 → 人工裁定

    @property
    def interventions(self) -> list[GuardrailFinding]:
        return [f for f in self.findings if f.blocked]


class CitationVerifier(Protocol):
    """引用查核介面（G5）。真實現於 O6.3（RAG）。"""

    def verify(self, citation: str) -> bool:
        """該引用是否存在於 RAG 庫且相似度過閾值。"""
        ...  # pragma: no cover

    @property
    def index_empty(self) -> bool:
        """RAG 庫是否為空（空 → G5 按 AI_BARE 語義：任何引用皆屬捏造）。"""
        ...  # pragma: no cover


@dataclass(frozen=True, slots=True)
class NoRagCitationVerifier:
    """預設查核器：RAG 尚未建置/為空——任何引用一律視為捏造（SPEC_FULL §9.0/§10 G5）。

    RAG 與 eval 目前皆空，這是系統的預設狀態，非錯誤。
    """

    def verify(self, citation: str) -> bool:
        return False

    @property
    def index_empty(self) -> bool:
        return True


def intervention_events(
    findings: Sequence[GuardrailFinding], tick: int, *, initiator_id: str | None = None
) -> list[LedgerEvent]:
    """把被攔截的 finding 轉為 GUARDRAIL_INTERVENTION Ledger 事件（證據性，入 hash）。"""
    return [
        LedgerEvent(
            event_type="GUARDRAIL_INTERVENTION",
            tick=tick,
            initiator_id=initiator_id,
            ai_decision={"check": f.check, "reason": f.reason, **f.detail},
        )
        for f in findings
        if f.blocked
    ]
