"""AI 運作模式解析與閘門（O6.2，SPEC_FULL §9.0）。"""

from __future__ import annotations

import pytest

from app.errors import AiDisabledError
from app.guardrails import require_ai_enabled, resolve_ai_mode
from app.models.enums import AiMode


def test_resolve_prefers_session_override() -> None:
    assert resolve_ai_mode("AI_FULL", "AI_OFF") is AiMode.AI_FULL


def test_resolve_falls_back_to_default() -> None:
    assert resolve_ai_mode(None, "AI_BARE") is AiMode.AI_BARE


def test_resolve_bad_value_defaults_off() -> None:
    assert resolve_ai_mode("garbage", "also-bad") is AiMode.AI_OFF


def test_require_ai_enabled_blocks_off() -> None:
    with pytest.raises(AiDisabledError):
        require_ai_enabled(AiMode.AI_OFF)


def test_require_ai_enabled_allows_bare_and_full() -> None:
    require_ai_enabled(AiMode.AI_BARE)
    require_ai_enabled(AiMode.AI_FULL)  # 不拋
