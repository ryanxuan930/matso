"""AI 運作模式解析與閘門（SPEC_FULL §9.0）。

session 未指定時回退到設定預設（Settings.ai_mode，env MATSO_AI_MODE，預設 AI_OFF）。
per-session 持久化欄位於 O6.5 落地；此處 `session_mode` 先接受可選字串 override。
"""

from __future__ import annotations

from app.errors import AiDisabledError
from app.models.enums import AiMode


def resolve_ai_mode(session_mode: str | None, default_mode: str) -> AiMode:
    """決定本次生效模式：session override 優先，否則設定預設；無法解析 → AI_OFF（保守）。"""
    for candidate in (session_mode, default_mode):
        if candidate:
            try:
                return AiMode(candidate)
            except ValueError:
                continue
    return AiMode.AI_OFF


def require_ai_enabled(mode: AiMode) -> None:
    """AI_OFF（傳統兵推）時拒絕 AI 功能——供 AI 端點/迴路在入口呼叫。"""
    if mode == AiMode.AI_OFF:
        raise AiDisabledError("此 session 為傳統兵推模式（AI_OFF），AI 功能不可用")
