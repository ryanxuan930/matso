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
import logging
import os
import threading
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

_LOG = logging.getLogger(__name__)

# 序列化對「單一本機模型」的呼叫：多陣營 worker 共用同一 decider/後端，若同時打一顆 12B 模型，
# 兩個大請求互搶 GPU → 每個都爆逾時。用行程級鎖讓 worker 執行緒輪流呼叫（各自拿滿算力）。
# vLLM/雲端有自身併發時，此鎖只是輕微序列化，不影響正確性。
_LLM_CALL_LOCK = threading.Lock()

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
    "order_type（MISSION/MOVE/ENGAGE/RECON/RESUPPLY/POSTURE/HOLD 擇一）、及目標：\n"
    "  ‣ MOVE：用 **target_lat + target_lng**（十進位經緯度）指定移動目的地——可填敵情/"
    "目標的座標以推進包抄。**要達成任務目標就必須靠 MOVE 機動**，勿只 ENGAGE 原地不動。\n"
    "    單位有速度上限（見各單位「機動：profile speed km/h」；步兵慢、機械化快），"
    "一個決策週期只能前進有限距離；**遠程目標請分多次 MOVE 逐步推進**，勿一次下超遠目的地。"
    "可加 tempo：FORCED_MARCH（強行軍，更快但耗損更高）／預設 NORMAL。\n"
    "    標有「剩餘行程 N km」者受**油料**限制：超過 N km 的移動會中途拋錨停駛，"
    "請勿下超出剩餘行程的目的地（必要時先就近集結/待補給）。\n"
    "  ‣ ENGAGE：用 target_unit_id 指向『已知敵情』的識別，可加 fire_policy"
    "（FREE/SMALL_ARMS_ONLY/ANTI_ARMOR_HOLD，預設 FREE）；僅在敵人於射程內才有效。\n"
    # WP-A2：任務級下令。**優先於逐令微操**——一道任務會由確定性的分解器持續展開成
    # MOVE/ENGAGE/POSTURE，不需要 LLM 每個心跳重新推理「下一步走哪」。
    "  ‣ **MISSION（優先使用）：下一道『任務』而不是逐步微操**。系統會自動把它展開成"
    "移動、接敵、佔領、構工等一連串動作並持續執行到完成，你不必每回合重下。\n"
    "    需 mission_type 與 params：\n"
    "      · SEIZE 奪佔：params={objective:{lat,lng}, axis:[{lat,lng},…]（選填，途經點）,"
    " objective_radius_m（選填，預設 500）} → 沿軸線機動→對目標區內敵接戰→佔領後轉守。\n"
    "      · DEFEND 防守：params={area:{lat,lng}, area_radius_m（選填）} → 就位→構工→"
    "對進入防區之敵接戰。\n"
    "      · SCREEN 掩護幕：params={line:[{lat,lng},…]} → 沿線佔位→偵測回報但**不接戰**。\n"
    "      · MOVE_MARCH 行軍：params={route:[{lat,lng},…]} → 依序通過航路點。\n"
    "    一個單位同時只該有一道任務。已有任務在執行中的單位**不要再下令**——"
    "任務會自己推進；要改變意圖才重下。\n"
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
        serialize_calls: bool = True,
        role_manager: Any = None,
    ) -> None:
        self._client = client
        self._model = model
        self._mode = _mode_str(mode)
        self._role = role
        self._adapter = adapter
        # 單一本機模型 → 序列化（多陣營 worker 輪流，不互搶 GPU）；雲端後端自有併發 → 免序列化。
        self._serialize = serialize_calls
        # WP-F3：有 RoleManager 就走它（佇列/優先級/adapter 攤銷/`AIInvocationLog` 稽核）。
        # None → 直連 client（既有行為，供不需要稽核的測試與離線工具用）。
        self._role_manager = role_manager
        self._session_id: str | None = None

    def bind_session(self, session_id: str | None) -> None:
        """綁定本 decider 服務的 session——稽核紀錄要能對回是哪一局的決策。"""
        self._session_id = session_id

    def decide(self, context: dict[str, Any], *, feedback: str | None = None) -> dict[str, Any]:
        system = build_system_prompt(self._role, self._mode)
        user = render_context_prompt(context) + OUTPUT_INSTRUCTION
        if feedback:
            user += _FEEDBACK_PREFIX + feedback
        if self._role_manager is not None:
            # ⚠ **一定要把 `system` 帶過去**。RoleManager 預設用註冊表的靜態 prompt，
            # 而這裡的是模式感知版本；換掉 prompt 會讓 `ReplayClient`（按 prompt 雜湊重播）
            # 的所有已錄自主場次在那一刻全部失效。
            from matso_ai.inference.role_manager import AIRequest

            request = AIRequest(
                role=self._role,
                user_prompt=user,
                session_id=self._session_id,
                system_prompt=system,
            )
            if self._serialize:
                with _LLM_CALL_LOCK:
                    result = self._role_manager.invoke(request)
            else:
                result = self._role_manager.invoke(request)
            return _extract_json(result.response.text)
        messages = [ChatMessage("system", system), ChatMessage("user", user)]
        if self._serialize:
            with _LLM_CALL_LOCK:
                response = self._client.complete(messages, model=self._model, adapter=self._adapter)
        else:
            response = self._client.complete(messages, model=self._model, adapter=self._adapter)
        return _extract_json(response.text)


# 本機後端主機名——這些走序列化鎖（單一 GPU）；其餘（雲端）併發呼叫。
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal", "::1")


def _is_local_backend(base_url: str) -> bool:
    from urllib.parse import urlparse

    host = (urlparse(base_url).hostname or base_url).lower()
    return any(h in host for h in _LOCAL_HOSTS)


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
    session_id: str | None = None,
    audit: bool = True,
) -> LlmFactionDecider:
    """由 #54 系統設定建 decider（OpenAI 相容後端）；record/replay 決定性見 build_llm_client。

    WP-F3：`audit=True`（預設）→ 呼叫走 `RoleManager`，每一次都落 `AIInvocationLog`
    （prompt hash / 模式 / 耗時）。**在此之前活自主迴路直連 client，一筆稽核都沒有**
    ——`RoleManager` 與 `InvocationLogWriter` 在 repo 裡的非測試引用是 0。
    """
    client = build_llm_client(
        base_url=base_url,
        api_key=api_key,
        model=model,
        replay_dir=replay_dir,
        record_dir=record_dir,
    )
    manager = _make_role_manager(client, model=model, mode=mode) if audit else None
    # 雲端後端（如 Google AI Studio）自有併發能力 → 不序列化；本機單一模型 → 序列化。
    decider = LlmFactionDecider(
        client,
        model=model,
        mode=mode,
        serialize_calls=_is_local_backend(base_url),
        role_manager=manager,
    )
    decider.bind_session(session_id)
    return decider


def _make_role_manager(client: Any, *, model: str, mode: AiMode | str) -> Any:
    """建 `RoleManager` + `InvocationLogWriter`（WP-F3）。

    建不起來（ai 套件缺席之類）→ **回 None 而不是拋**：稽核掛掉不該讓整個自主推演停擺，
    但**要留 log**，否則「為什麼沒有稽核紀錄」會變成一個無跡可循的問題。
    """
    try:
        from app.db import default_session_factory
        from matso_ai.inference.invocation_log import InvocationLogWriter
        from matso_ai.inference.role_manager import RoleManager

        return RoleManager(
            client,
            log_writer=InvocationLogWriter(default_session_factory()),
            model=model,
            mode=_mode_str(mode),
        )
    except Exception:
        _LOG.warning("RoleManager 建立失敗，AI 呼叫將不落稽核紀錄", exc_info=True)
        return None
