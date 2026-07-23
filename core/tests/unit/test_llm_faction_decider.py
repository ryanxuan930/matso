"""O11.2 LlmFactionDecider：JSON 擷取、陣營中性 prompt、feedback、與 run_faction_turn 端到端。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest

from app.ai_loop import run_faction_turn
from app.ai_loop.context import build_faction_context
from app.ai_loop.decider import (
    LlmFactionDecider,
    _extract_json,
    _is_local_backend,
    build_llm_client,
    make_llm_faction_decider,
)
from app.factions.relations import FactionRelations, Relation
from app.guardrails import GuardrailGateway
from app.models.enums import AiMode
from matso_ai.inference.client import (
    ChatMessage,
    LLMResponse,
    OpenAICompatibleClient,
    RecordingClient,
    ReplayClient,
)

# 一份合法 opfor_decision（reasoning_chain 3 個行首編號步驟 + ≥80 字；cited 空＝AI_BARE 相容）。
_VALID = {
    "reasoning_chain": (
        "1. 判斷態勢：偵測到敵 r1 為裝甲目標，位於我方 b1 有效射程內，威脅本陣營任務。\n"
        "2. 確立意圖：集中火力先行殲滅該裝甲單位，解除對前沿的直接威脅。\n"
        "3. 配置命令：令 b1 以聯合火力對 r1 接戰，其餘單位維持現況待命。"
    ),
    "confidence": 0.72,
    "cited_documents": [],
    "intent": "殲滅前沿裝甲",
    "orders": [
        {"unit_id": "b1", "order_type": "ENGAGE", "target_unit_id": "r1", "fire_policy": "FREE"}
    ],
    "ihl_self_check": {"civilian_risk_assessed": True},
}


class _FakeClient:
    """記錄最後一次 messages 的假 LLM client（回傳固定文字）。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.last_messages: list[ChatMessage] = []
        self.last_adapter = ""

    def complete(self, messages: Sequence[ChatMessage], *, model: str, adapter: str) -> LLMResponse:
        self.last_messages = list(messages)
        self.last_adapter = adapter
        return LLMResponse(text=self.text, tokens_in=1, tokens_out=1, model=model, adapter=adapter)


def _ctx() -> dict[str, Any]:
    return build_faction_context(
        faction="BLUFOR",
        tick=3,
        hot_snapshot={"b1": {"lat": 24.1, "lng": 121.1, "strength": 100.0, "health": 100.0}},
        unit_meta={},  # 不影響 decider（context 已建好）
        known_enemies=[{"unit_id": "r1", "lat": 24.2, "lng": 121.2}],
        relations=FactionRelations([("BLUFOR", "OPFOR", Relation.HOSTILE)]),
        mission="殲滅當面之敵",
    )


def _decider(text: str, mode: AiMode = AiMode.AI_BARE) -> tuple[LlmFactionDecider, _FakeClient]:
    client = _FakeClient(text)
    return LlmFactionDecider(client, model="gemma", mode=mode), client


# ---- _extract_json ----


def test_extract_plain_json() -> None:
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_fenced_json() -> None:
    assert _extract_json('```json\n{"a": 2}\n```') == {"a": 2}


def test_extract_prose_wrapped_json() -> None:
    assert _extract_json('決策如下：{"a": 3} 完成。') == {"a": 3}


def test_extract_garbage_returns_empty() -> None:
    assert _extract_json("抱歉，我無法產生指令。") == {}


def test_extract_json_array_returns_empty() -> None:
    # 頂層是陣列不是物件 → 視為無效（decision 必為物件）。
    assert _extract_json("[1, 2, 3]") == {}


# ---- decide() ----


def test_decide_parses_and_returns_dict() -> None:
    decider, _ = _decider(json.dumps(_VALID, ensure_ascii=False))
    out = decider.decide(_ctx())
    assert out["orders"][0]["unit_id"] == "b1"


def test_system_prompt_is_faction_neutral() -> None:
    decider, client = _decider(json.dumps(_VALID, ensure_ascii=False))
    decider.decide(_ctx())
    system = client.last_messages[0].content
    user = client.last_messages[1].content
    assert "briefing" in system  # 身分由 briefing 注入，非硬編紅/藍
    assert "你指揮陣營：BLUFOR" in user  # 態勢已渲染進 user
    assert "只**輸出一個 JSON" in user  # 帶輸出格式指示
    assert client.last_adapter == "base"  # 單一模型定址（切換成本 0）


def test_bare_mode_citation_clause() -> None:
    decider, client = _decider(json.dumps(_VALID, ensure_ascii=False), mode=AiMode.AI_BARE)
    decider.decide(_ctx())
    assert "必須為空陣列" in client.last_messages[0].content  # AI_BARE 引用條款


def test_feedback_appended_to_user_prompt() -> None:
    decider, client = _decider(json.dumps(_VALID, ensure_ascii=False))
    decider.decide(_ctx(), feedback="G1：orders 缺 unit_id")
    assert "G1：orders 缺 unit_id" in client.last_messages[1].content


# ---- 與 run_faction_turn 端到端 ----


def test_run_faction_turn_accepts_valid_decision() -> None:
    decider, _ = _decider(json.dumps(_VALID, ensure_ascii=False), mode=AiMode.AI_BARE)
    result = run_faction_turn(
        decider,
        GuardrailGateway(),
        mode=AiMode.AI_BARE,
        context=_ctx(),
    )
    assert result.accepted is True
    assert result.orders[0]["unit_id"] == "b1"
    assert result.fallback_used is False


def test_run_faction_turn_falls_back_on_garbage() -> None:
    decider, _ = _decider("我無法決策", mode=AiMode.AI_BARE)
    result = run_faction_turn(
        decider,
        GuardrailGateway(),
        mode=AiMode.AI_BARE,
        context=_ctx(),
    )
    assert result.accepted is False
    assert result.orders == []
    assert result.fallback_used is True  # G1 擋下 → 重試耗盡 → doctrine fallback（HOLD）


# ---- O11.6 決定性重播 ----


@pytest.fixture(autouse=True)
def _clear_replay_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MATSO_LLM_REPLAY_DIR", raising=False)
    monkeypatch.delenv("MATSO_LLM_RECORD_DIR", raising=False)


def test_build_client_selects_replay(tmp_path: Any) -> None:
    c = build_llm_client(base_url="http://x", api_key="", model="m", replay_dir=str(tmp_path))
    assert isinstance(c, ReplayClient)


def test_build_client_selects_recording(tmp_path: Any) -> None:
    c = build_llm_client(base_url="http://x", api_key="", model="m", record_dir=str(tmp_path))
    assert isinstance(c, RecordingClient)


def test_build_client_defaults_real() -> None:
    c = build_llm_client(base_url="http://x", api_key="", model="m")
    assert isinstance(c, OpenAICompatibleClient)


def test_record_then_replay_is_deterministic(tmp_path: Any) -> None:
    ctx = _ctx()
    # 錄：RecordingClient 包一個假「真」client，把回應寫成 fixture。
    fake_real = _FakeClient(json.dumps(_VALID, ensure_ascii=False))
    rec = LlmFactionDecider(
        RecordingClient(inner=fake_real, out_dir=tmp_path), model="gemma", mode=AiMode.AI_BARE
    )
    out1 = rec.decide(ctx)
    # 放：ReplayClient 只讀 fixture，不碰任何真 client（air-gapped/CI）。
    rep = LlmFactionDecider(ReplayClient.from_dir(tmp_path), model="gemma", mode=AiMode.AI_BARE)
    out2 = rep.decide(ctx)
    assert out1 == out2
    assert out2["orders"][0]["unit_id"] == "b1"  # 同 context → 同決策（決定性）


# ---- 雲端後端（Google AI Studio）序列化策略 ----


def test_is_local_backend() -> None:
    assert _is_local_backend("http://host.docker.internal:11434") is True
    assert _is_local_backend("http://localhost:11434") is True
    assert _is_local_backend("https://generativelanguage.googleapis.com/v1beta/openai") is False


def test_decider_serializes_local_not_cloud() -> None:
    local = make_llm_faction_decider(base_url="http://localhost:11434", model="m")
    cloud = make_llm_faction_decider(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai", model="m", api_key="k"
    )
    assert local._serialize is True  # 本機單一模型 → 序列化
    assert cloud._serialize is False  # 雲端自有併發 → 免序列化
