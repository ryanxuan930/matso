"""OPFOR 自主決策迴路（SPEC_FULL §9.1/§9.2/§10）。

一個「回合」：decider 產生決策 → GuardrailGateway 依序過 G1–G6 → 不過則附回饋重試（≤2）→
仍不過則 doctrine fallback（空令＝不行動，安全）。回傳清洗後的 order 清單供上層落為 pending，
與護欄 finding（上層轉 GUARDRAIL_INTERVENTION 事件）。

模式感知（§9.0）：AI_OFF → 迴路拒絕啟動（require_ai_enabled 拋 AiDisabledError，紅軍由人操作）。
decider 為注入介面：真實現＝RoleManager + build_system_prompt（部署層 core↔ai 接線），此模組只依賴
抽象 decider，保持 core 與 ai 子系統零載入期耦合、可決定性測試。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.guardrails import GuardrailGateway, require_ai_enabled
from app.guardrails.gateway import OrderFeasibilityChecker
from app.guardrails.schemas import CitationVerifier, GuardrailFinding
from app.models.enums import AiMode

DEFAULT_MAX_RETRIES = 2  # §9.2：schema 驗證失敗最多重試 2 次 → fallback


class OpforDecider(Protocol):
    """AI 決策來源。context＝戰場狀態摘要；feedback＝上一輪護欄回饋（重試用）。"""

    def decide(
        self, context: dict[str, Any], *, feedback: str | None = None
    ) -> dict[str, Any]: ...  # pragma: no cover


@dataclass(frozen=True, slots=True)
class AiTurnResult:
    accepted: bool
    orders: list[dict[str, Any]]  # 清洗後、待落為 pending 的 order
    findings: list[GuardrailFinding]
    escalate_white_cell: bool = False
    fallback_used: bool = False
    attempts: int = 0

    @property
    def interventions(self) -> list[GuardrailFinding]:
        return [f for f in self.findings if f.blocked]


def run_opfor_turn(
    decider: OpforDecider,
    gateway: GuardrailGateway,
    *,
    mode: AiMode,
    context: dict[str, Any],
    schema_ref: str = "opfor_decision",
    no_strike_hexes: frozenset[str] = frozenset(),
    feasibility: OrderFeasibilityChecker | None = None,
    citation_verifier: CitationVerifier | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> AiTurnResult:
    """跑一個 OPFOR 回合。AI_OFF → AiDisabledError（上層據此讓紅軍由人操作）。"""
    require_ai_enabled(mode)

    feedback: str | None = None
    findings: list[GuardrailFinding] = []
    escalate = False
    for attempt in range(1, max_retries + 2):
        output = decider.decide(context, feedback=feedback)
        outcome = gateway.evaluate(
            output,
            schema_ref=schema_ref,
            mode=mode,
            no_strike_hexes=no_strike_hexes,
            feasibility=feasibility,
            citation_verifier=citation_verifier,
        )
        findings = outcome.findings
        escalate = outcome.escalate_white_cell
        if outcome.accepted:
            orders = list((outcome.sanitized or {}).get("orders", []) or [])
            return AiTurnResult(
                accepted=True,
                orders=orders,
                findings=findings,
                escalate_white_cell=escalate,
                attempts=attempt,
            )
        feedback = "；".join(f.reason for f in outcome.interventions) or "輸出不合規"

    # 重試耗盡 → doctrine fallback：不下令（安全預設）。上層記 AI_OUTPUT_REJECTED。
    return AiTurnResult(
        accepted=False,
        orders=[],
        findings=findings,
        escalate_white_cell=escalate,
        fallback_used=True,
        attempts=max_retries + 1,
    )
