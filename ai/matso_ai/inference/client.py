"""OpenAI-compatible LLM client（SPEC_FULL §9.1）+ 錄放 mock。

- `OpenAICompatibleClient`：真部署用。POST `<base>/v1/chat/completions`；base_url/api_key/model
  一律由建構參數或環境變數（`OPENAI_BASE_URL` / `OPENAI_API_KEY` / `MATSO_LLM_MODEL`）注入，
  無硬編碼。vLLM 以 `model` 欄位定址 LoRA adapter，故 adapter≠"base" 時以 adapter 名當 model。
- `ReplayClient`：air-gapped / CI 用。以 prompt 雜湊查已錄回應，不需網路與 GPU。
- `RecordingClient`：包一個真 client，把回應寫成 fixtures 供日後重播（使用者有本機 vLLM 時錄一次）。
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

_FIXTURE_VERSION = 1


@dataclass(frozen=True)
class ChatMessage:
    """單則對話訊息。role ∈ {"system","user","assistant"}。"""

    role: str
    content: str


@dataclass(frozen=True)
class LLMResponse:
    """一次補全的結果。"""

    text: str
    tokens_in: int
    tokens_out: int
    model: str
    adapter: str


def prompt_hash(messages: Sequence[ChatMessage], model: str, adapter: str) -> str:
    """對 (model, adapter, messages) 取正規化 SHA-256——錄放鍵與 AIInvocationLog.promptHash。"""
    canonical = json.dumps(
        {
            "model": model,
            "adapter": adapter,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@runtime_checkable
class LLMClient(Protocol):
    """推論 client 介面。RoleManager 只依賴此協定，故真 client 與 mock 可互換。"""

    def complete(
        self, messages: Sequence[ChatMessage], *, model: str, adapter: str
    ) -> LLMResponse: ...


class MissingRecordingError(RuntimeError):
    """ReplayClient 找不到對應 prompt 雜湊的錄音（需先以 RecordingClient 錄製）。"""

    def __init__(self, digest: str, model: str, adapter: str) -> None:
        super().__init__(
            f"no recording for prompt_hash={digest[:12]}… (model={model!r}, adapter={adapter!r})"
        )
        self.digest = digest


class OpenAICompatibleClient:
    """呼叫 OpenAI-compatible endpoint（本機 vLLM）。CI 不觸及（無網路）。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        http_client: Any | None = None,  # 注入 httpx.Client（測試/連線池共用）
    ) -> None:
        self._base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "")).rstrip("/")
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._model = model or os.environ.get("MATSO_LLM_MODEL", "")
        self._timeout = timeout
        self._http = http_client

    def _client(self) -> Any:
        if self._http is None:
            import httpx

            self._http = httpx.Client(timeout=self._timeout)
        return self._http

    def complete(self, messages: Sequence[ChatMessage], *, model: str, adapter: str) -> LLMResponse:
        if not self._base_url:
            raise RuntimeError("OPENAI_BASE_URL 未設定——無法呼叫真模型（CI 應改用 ReplayClient）")
        target_model = adapter if adapter and adapter != "base" else (model or self._model)
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        payload = {
            "model": target_model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        resp = self._client().post(
            f"{self._base_url}/v1/chat/completions", json=payload, headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
        text = str(data["choices"][0]["message"]["content"])
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            model=target_model,
            adapter=adapter,
        )


@dataclass
class ReplayClient:
    """以 prompt 雜湊重播已錄回應（air-gapped / CI）。"""

    responses: dict[str, LLMResponse]

    def complete(self, messages: Sequence[ChatMessage], *, model: str, adapter: str) -> LLMResponse:
        digest = prompt_hash(messages, model, adapter)
        try:
            return self.responses[digest]
        except KeyError:
            raise MissingRecordingError(digest, model, adapter) from None

    @classmethod
    def from_dir(cls, directory: str | Path) -> ReplayClient:
        """從目錄載入所有 `*.json` fixture。"""
        responses: dict[str, LLMResponse] = {}
        for path in sorted(Path(directory).glob("*.json")):
            rec = json.loads(path.read_text(encoding="utf-8"))
            r = rec["response"]
            responses[rec["prompt_hash"]] = LLMResponse(
                text=r["text"],
                tokens_in=int(r["tokens_in"]),
                tokens_out=int(r["tokens_out"]),
                model=r["model"],
                adapter=r["adapter"],
            )
        return cls(responses)


@dataclass
class RecordingClient:
    """包一個真 client：呼叫後把 (request, response) 寫成 fixture 供日後重播。"""

    inner: LLMClient
    out_dir: Path

    def __post_init__(self) -> None:
        self.out_dir = Path(self.out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def complete(self, messages: Sequence[ChatMessage], *, model: str, adapter: str) -> LLMResponse:
        response = self.inner.complete(messages, model=model, adapter=adapter)
        digest = prompt_hash(messages, model, adapter)
        fixture = {
            "version": _FIXTURE_VERSION,
            "prompt_hash": digest,
            "request": {
                "model": model,
                "adapter": adapter,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
            },
            "response": {
                "text": response.text,
                "tokens_in": response.tokens_in,
                "tokens_out": response.tokens_out,
                "model": response.model,
                "adapter": response.adapter,
            },
        }
        (self.out_dir / f"{digest}.json").write_text(
            json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return response
