"""LlmFactionDecider — O11.2（SPEC_AUTONOMY §4）：把 LLM 接成 run_faction_turn 的 decider。

實作 `OpforDecider` 協定：`decide(context, feedback) → dict`。以 `build_system_prompt`（角色本體
+ 模式引用條款）作 system、`context.render_context_prompt` + 輸出格式指示作 user，打 OpenAI 相容
後端（Ollama/vLLM），把回傳文字解析為 opfor_decision dict 交給護欄 G1 驗證。

陣營中性：陣營身分/任務/敵情全在 context（briefing）內，故單一 decider 以 FACTION_COMMANDER
角色 + per-faction context 服務任一陣營；單一本機模型 → adapter="base" 定址，切換成本 0。

紅線：本類**只產生 order dict**，不裁決物理、不寫熱狀態；輸出仍走 run_faction_turn 的護欄與
物理預檢。解析失敗回 {}（G1 擋下 → 重試 → doctrine fallback HOLD，安全）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.ai_loop.context import render_context_prompt
from app.models.enums import AiMode
from matso_ai.inference.client import (
    ChatMessage,
    LLMClient,
    OpenAICompatibleClient,
    RecordingClient,
    ReplayClient,
)
from matso_ai.prompts import build_system_prompt
from matso_ai.roles import Role

# LLM 必須輸出的 opfor_decision 結構（對齊 contracts/ai_output.schema.json；G1/G2 據此驗）。
OUTPUT_INSTRUCTION = (
    "\n\n———\n"
    "請**只**輸出一個 JSON 物件（不要 markdown 圍欄、不要多餘說明文字），欄位：\n"
    "- reasoning_chain：**單一字串（不是陣列）**，用換行分隔至少 3 個編號步驟"
    '（例："1. …\\n2. …\\n3. …"），至少 80 字。\n'
    "- confidence：0~1 之間的數字。\n"
    "- cited_documents：字串陣列（無 RAG 準則時填 []）。\n"
    "- intent：一句話總意圖。\n"
    "- orders：命令陣列；每個物件含 unit_id（必為『我方部隊』的 unit_id）、"
    "order_type（MOVE/ENGAGE/RECON/RESUPPLY/POSTURE/HOLD 擇一）、"
    "及目標（ENGAGE 用 target_unit_id 指向『已知敵情』的識別；MOVE 用 target_h3）；"
    "ENGAGE 可加 fire_policy（FREE/SMALL_ARMS_ONLY/ANTI_ARMOR_HOLD，預設 FREE）。\n"
    "- ihl_self_check：物件，含 civilian_risk_assessed（true/false）。\n"
    "若本回合無適當行動，orders 可為空陣列。"
)

_FEEDBACK_PREFIX = "\n\n【上一輪輸出未通過護欄，請依此修正後重試】"


def _mode_str(mode: AiMode | str) -> str:
    return mode.value if isinstance(mode, AiMode) else str(mode)


def _outermost_object(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else ""


def _extract_json(text: str) -> dict[str, Any]:
    """從 LLM 回傳文字擷取 JSON 物件。容忍 markdown 圍欄與前後雜訊；失敗回 {}（G1 會擋）。"""
    stripped = text.strip()
    candidates = [stripped]
    if "```" in stripped:
        parts = stripped.split("```")
        if len(parts) >= 3:
            body = parts[1]
            if body.lstrip().lower().startswith("json"):
                body = body.lstrip()[4:]
            candidates.append(body.strip())
    candidates.append(_outermost_object(stripped))
    for cand in candidates:
        if not cand:
            continue
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return {}


class LlmFactionDecider:
    """OpforDecider 實作：LLM → opfor_decision dict。陣營身分由 context 注入。"""

    def __init__(
        self,
        client: LLMClient,
        *,
        model: str,
        mode: AiMode | str,
        role: Role = Role.FACTION_COMMANDER,
        adapter: str = "base",
    ) -> None:
        self._client = client
        self._model = model
        self._mode = _mode_str(mode)
        self._role = role
        self._adapter = adapter

    def decide(self, context: dict[str, Any], *, feedback: str | None = None) -> dict[str, Any]:
        system = build_system_prompt(self._role, self._mode)
        user = render_context_prompt(context) + OUTPUT_INSTRUCTION
        if feedback:
            user += _FEEDBACK_PREFIX + feedback
        messages = [ChatMessage("system", system), ChatMessage("user", user)]
        response = self._client.complete(messages, model=self._model, adapter=self._adapter)
        return _extract_json(response.text)


# LLM 呼叫逾時（O11.8）：超時 → 拋錯 → worker fallback HOLD，不卡 worker 執行緒。
# 本機 12B 級模型產完整 JSON 決策可達 ~80s+，故預設放寬到 180s，並可用 env 覆寫。
_LLM_TIMEOUT_S = float(os.environ.get("MATSO_LLM_TIMEOUT_S", "180"))


def build_llm_client(
    *,
    base_url: str,
    api_key: str,
    model: str,
    replay_dir: str | None = None,
    record_dir: str | None = None,
    timeout: float = _LLM_TIMEOUT_S,
) -> LLMClient:
    """依決定性策略挑 client（O11.6，SPEC_AUTONOMY §6）：

    - `replay_dir`（或 env `MATSO_LLM_REPLAY_DIR`）：`ReplayClient`——按 prompt 雜湊重播已錄回應，
      **零網路/零 GPU**（air-gapped / CI / golden 自主場次）。
    - `record_dir`（或 env `MATSO_LLM_RECORD_DIR`）：`RecordingClient` 包真 client，回應寫成 fixture
      （使用者本機 Ollama 錄一次）。
    - 皆無：真 `OpenAICompatibleClient`。
    """
    replay = replay_dir or os.environ.get("MATSO_LLM_REPLAY_DIR") or ""
    record = record_dir or os.environ.get("MATSO_LLM_RECORD_DIR") or ""
    if replay:
        return ReplayClient.from_dir(replay)
    real = OpenAICompatibleClient(base_url=base_url, api_key=api_key, model=model, timeout=timeout)
    if record:
        return RecordingClient(inner=real, out_dir=Path(record))
    return real


def make_llm_faction_decider(
    *,
    base_url: str,
    model: str,
    api_key: str = "",
    mode: AiMode | str = AiMode.AI_BARE,
    replay_dir: str | None = None,
    record_dir: str | None = None,
) -> LlmFactionDecider:
    """由 #54 系統設定建 decider（OpenAI 相容後端）；record/replay 決定性見 build_llm_client。"""
    client = build_llm_client(
        base_url=base_url,
        api_key=api_key,
        model=model,
        replay_dir=replay_dir,
        record_dir=record_dir,
    )
    return LlmFactionDecider(client, model=model, mode=mode)
