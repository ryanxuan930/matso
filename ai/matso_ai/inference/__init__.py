"""AI 推論管線（SPEC_FULL §9.1）：OpenAI-compatible client + RoleManager。

真部署指向本機 vLLM（`OPENAI_BASE_URL`）；air-gapped / CI 以錄放 mock（ReplayClient）取代。
"""

from __future__ import annotations

from matso_ai.inference.client import (
    ChatMessage,
    LLMClient,
    LLMResponse,
    MissingRecordingError,
    OpenAICompatibleClient,
    RecordingClient,
    ReplayClient,
    prompt_hash,
)
from matso_ai.inference.invocation_log import InvocationLogWriter, InvocationRecord
from matso_ai.inference.role_manager import AIRequest, AIResult, RoleManager

__all__ = [
    "AIRequest",
    "AIResult",
    "ChatMessage",
    "InvocationLogWriter",
    "InvocationRecord",
    "LLMClient",
    "LLMResponse",
    "MissingRecordingError",
    "OpenAICompatibleClient",
    "RecordingClient",
    "ReplayClient",
    "RoleManager",
    "prompt_hash",
]
