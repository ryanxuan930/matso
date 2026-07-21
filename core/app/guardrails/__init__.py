"""Guardrail Gateway（SPEC_FULL §10）——AI Node 與 Ledger 之間的強制閘道。

任何 AI 輸出 MUST 依序過 G1–G6。攔截即 GUARDRAIL_INTERVENTION 事件（AAR 統計 AI 可靠度）。
本套件屬 **core**（受信任側），不在 ai 子系統——AI 永不自我裁決是否合規（紅線）。
"""

from __future__ import annotations

from app.guardrails.gateway import GuardrailGateway
from app.guardrails.modes import require_ai_enabled, resolve_ai_mode
from app.guardrails.profiles import GuardrailProfile, load_profile
from app.guardrails.schemas import (
    GuardrailFinding,
    GuardrailOutcome,
    NoRagCitationVerifier,
    intervention_events,
)

__all__ = [
    "GuardrailFinding",
    "GuardrailGateway",
    "GuardrailOutcome",
    "GuardrailProfile",
    "NoRagCitationVerifier",
    "intervention_events",
    "load_profile",
    "require_ai_enabled",
    "resolve_ai_mode",
]
