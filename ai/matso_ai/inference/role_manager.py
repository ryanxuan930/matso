"""RoleManager（SPEC_FULL §9.1）：角色分組批次佇列 + OPFOR 優先 + AIInvocationLog 記錄。

單節點 role-switching：切 adapter 有成本（LoRA swap ~秒級），故 MUST 以角色分組批次處理以攤銷
切換；`OPFOR_COMMANDER` 佇列優先權最高（維持對抗即時性）。所有呼叫記入 AIInvocationLog。

latency 用**注入時鐘**（比照 JwtCodec/Kernel 慣例；預設 `time.perf_counter`），測試可決定性替換；
latency 只進 side log，不進 Ledger hash（R8 教訓：非決定性診斷不入被 hash 的狀態）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from matso_ai.inference.client import ChatMessage, LLMClient, LLMResponse, prompt_hash
from matso_ai.inference.invocation_log import InvocationLogWriter, InvocationRecord
from matso_ai.roles import ROLE_REGISTRY, Role, RoleConfig, UnknownRoleError

_LOG = logging.getLogger("matso_ai.role_manager")

DEFAULT_MAX_QUEUE = 1000  # 與 WS BoundedSender 同量級（背壓一致性，CODE_REVIEW C9）


class QueueFullError(RuntimeError):
    """佇列已達上限——呼叫端應退避（背壓）。"""


@dataclass(frozen=True)
class AIRequest:
    """一筆待處理的 AI 請求。"""

    role: Role
    user_prompt: str
    session_id: str | None = None
    request_id: str | None = None  # 呼叫端關聯用（不影響處理）


@dataclass(frozen=True)
class AIResult:
    """一次處理結果。"""

    request: AIRequest
    response: LLMResponse
    latency_ms: int
    log_id: str | None


class RoleManager:
    """角色註冊表 + 佇列 + adapter 熱切換攤銷。"""

    def __init__(
        self,
        client: LLMClient,
        *,
        registry: Mapping[Role, RoleConfig] = ROLE_REGISTRY,
        log_writer: InvocationLogWriter | None = None,
        clock: Callable[[], float] = time.perf_counter,
        model: str = "",
        max_queue: int = DEFAULT_MAX_QUEUE,
    ) -> None:
        self._client = client
        self._registry = dict(registry)
        self._log = log_writer
        self._clock = clock
        self._model = model
        self._max_queue = max_queue
        self._queue: list[AIRequest] = []
        self._adapter_swaps = 0
        self._current_adapter: str | None = None

    @property
    def adapter_swaps(self) -> int:
        """累計 adapter 切換次數（分組批次應遠小於請求數）。"""
        return self._adapter_swaps

    @property
    def pending(self) -> int:
        return len(self._queue)

    def enqueue(self, request: AIRequest) -> None:
        """入列（延後處理）。未知角色 → UnknownRoleError；佇列滿 → QueueFullError（背壓，C9）。"""
        if request.role not in self._registry:
            raise UnknownRoleError(request.role)
        if len(self._queue) >= self._max_queue:
            raise QueueFullError(f"AI 佇列已滿（上限 {self._max_queue}）")
        self._queue.append(request)

    def process_pending(self) -> list[AIResult]:
        """處理佇列全部請求：依角色 priority 由高到低分組（OPFOR 最高），組內維持 enqueue FIFO。

        同角色請求排序後相鄰 → 共用一次 adapter，攤銷熱切換。單筆失敗不中斷整批（記 log 後跳過，
        CODE_REVIEW C10）；**佇列一律清空**（回傳依實際處理順序的成功結果）。
        """
        ordered = sorted(
            enumerate(self._queue),
            key=lambda iq: (-self._registry[iq[1].role].priority, iq[0]),
        )
        results: list[AIResult] = []
        try:
            for _, req in ordered:
                try:
                    results.append(self._invoke(req))
                except Exception:  # 單筆失敗隔離，不拖垮整批
                    _LOG.warning("AI 請求失敗（role=%s）已跳過", req.role.value, exc_info=True)
        finally:
            self._queue.clear()
        return results

    def invoke(self, request: AIRequest) -> AIResult:
        """單發（不經佇列）。仍計入 adapter 切換與 log。"""
        if request.role not in self._registry:
            raise UnknownRoleError(request.role)
        return self._invoke(request)

    def _invoke(self, request: AIRequest) -> AIResult:
        cfg = self._registry[request.role]
        if self._current_adapter != cfg.adapter:
            self._adapter_swaps += 1
            self._current_adapter = cfg.adapter
        messages = [
            ChatMessage("system", cfg.system_prompt),
            ChatMessage("user", request.user_prompt),
        ]
        start = self._clock()
        response = self._client.complete(messages, model=self._model, adapter=cfg.adapter)
        latency_ms = int((self._clock() - start) * 1000)

        log_id: str | None = None
        if self._log is not None:
            log_id = self._log.record(
                InvocationRecord(
                    role=request.role.value,
                    adapter=cfg.adapter,
                    prompt_hash=prompt_hash(messages, self._model, cfg.adapter),
                    request={
                        "model": self._model,
                        "adapter": cfg.adapter,
                        "messages": [{"role": m.role, "content": m.content} for m in messages],
                    },
                    response={"text": response.text},
                    latency_ms=latency_ms,
                    tokens_in=response.tokens_in,
                    tokens_out=response.tokens_out,
                    guardrail_result={"status": "not_evaluated"},  # O6.2 護欄填
                    session_id=request.session_id,
                )
            )
        return AIResult(request=request, response=response, latency_ms=latency_ms, log_id=log_id)
