"""LLM client + 錄放 mock（O6.1）。"""

from __future__ import annotations

import httpx
import pytest

from matso_ai.inference.client import (
    ChatMessage,
    LLMResponse,
    MissingRecordingError,
    OpenAICompatibleClient,
    RecordingClient,
    ReplayClient,
    prompt_hash,
)

MSGS = [ChatMessage("system", "你是紅軍指揮官"), ChatMessage("user", "敵在 H-45")]


def test_prompt_hash_is_deterministic_and_adapter_sensitive() -> None:
    h1 = prompt_hash(MSGS, "m", "opfor-v1")
    h2 = prompt_hash(MSGS, "m", "opfor-v1")
    h3 = prompt_hash(MSGS, "m", "planner-v1")
    assert h1 == h2
    assert h1 != h3  # adapter 不同 → 不同鍵


def test_replay_client_returns_recorded_and_raises_on_miss() -> None:
    digest = prompt_hash(MSGS, "m", "opfor-v1")
    canned = LLMResponse("撤退", tokens_in=10, tokens_out=3, model="m", adapter="opfor-v1")
    client = ReplayClient({digest: canned})

    assert client.complete(MSGS, model="m", adapter="opfor-v1") is canned
    with pytest.raises(MissingRecordingError):
        client.complete(MSGS, model="m", adapter="unseen")


def test_recording_then_replay_roundtrip(tmp_path) -> None:
    canned = LLMResponse("固守", tokens_in=7, tokens_out=2, model="m", adapter="opfor-v1")

    class _Stub:
        def complete(self, messages, *, model, adapter):  # type: ignore[no-untyped-def]
            return canned

    rec = RecordingClient(inner=_Stub(), out_dir=tmp_path)
    rec.complete(MSGS, model="m", adapter="opfor-v1")

    replay = ReplayClient.from_dir(tmp_path)
    got = replay.complete(MSGS, model="m", adapter="opfor-v1")
    assert (got.text, got.tokens_in, got.tokens_out) == ("固守", 7, 2)


def test_openai_compatible_client_offline_via_mock_transport() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = httpx.Response(200)  # placeholder
        import json as _json

        body = _json.loads(request.content)
        seen["model"] = body["model"]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "遲滯"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = OpenAICompatibleClient(base_url="http://ai-node:8000", model="gemma", http_client=http)

    out = client.complete(MSGS, model="gemma", adapter="opfor-v1")
    assert out.text == "遲滯"
    assert (out.tokens_in, out.tokens_out) == (12, 4)
    assert seen["url"] == "http://ai-node:8000/v1/chat/completions"
    assert seen["model"] == "opfor-v1"  # adapter≠base → 以 adapter 定址 vLLM model


def test_openai_client_without_base_url_refuses() -> None:
    client = OpenAICompatibleClient(base_url="", model="m")
    with pytest.raises(RuntimeError):
        client.complete(MSGS, model="m", adapter="opfor-v1")
