"""AI 角色註冊表（SPEC_FULL §9.1）。

5 個 Phase 1 角色，各自帶 system prompt、LoRA adapter 版本、佇列優先權、輸出 schema 對照。
RoleManager 依 `priority` 決定佇列處理順序（`OPFOR_COMMANDER` 最高，維持對抗即時性），
並以 `adapter` 分組批次處理以攤銷熱切換成本。

註：
- 本卡（O6.1）的 `system_prompt` 為精簡佔位；正式各角色 prompt 於 O6.4 落在 `ai/prompts/`。
- `adapter` 為 LoRA adapter 版本標記。本機無 LoRA 的部署可將所有角色 override 為單一 adapter
  （見 RoleManager；此時分組批次的切換成本自然歸零），但註冊表以「設計上的 per-role adapter」表示。
- `output_schema_ref` 對應 `contracts/ai_output.schema.json` 的 $def 名（供 O6.2 護欄 G1 驗證）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    """Phase 1 五角色（SPEC_FULL §9.1）。"""

    STRATEGIC_PLANNER = "STRATEGIC_PLANNER"
    OPFOR_COMMANDER = "OPFOR_COMMANDER"
    AAR_ANALYST = "AAR_ANALYST"
    INTEL_OFFICER = "INTEL_OFFICER"
    WHITE_CELL_ASSISTANT = "WHITE_CELL_ASSISTANT"


@dataclass(frozen=True)
class RoleConfig:
    """單一角色的推論設定。"""

    role: Role
    system_prompt: str
    adapter: str
    priority: int  # 數字越大越優先；OPFOR_COMMANDER 最高
    output_schema_ref: str  # contracts/ai_output.schema.json 的 $def 名


class UnknownRoleError(KeyError):
    """請求角色不在註冊表中。"""

    def __init__(self, role: object) -> None:
        super().__init__(role)
        self.role = role


# 佔位 system prompt（O6.4 以 ai/prompts/ 正式檔取代）。刻意精簡但點出職責與輸出契約。
_PLACEHOLDER = "（O6.4 正式 prompt 待補）你必須輸出通過 JSON Schema 驗證的結構化指令，"

ROLE_REGISTRY: dict[Role, RoleConfig] = {
    Role.OPFOR_COMMANDER: RoleConfig(
        role=Role.OPFOR_COMMANDER,
        system_prompt=_PLACEHOLDER + "身為紅軍指揮官，依紅軍準則對戰場變化決策並產令。",
        adapter="opfor-v1",
        priority=100,  # 對抗即時性最高
        output_schema_ref="opfor_decision",
    ),
    Role.STRATEGIC_PLANNER: RoleConfig(
        role=Role.STRATEGIC_PLANNER,
        system_prompt=_PLACEHOLDER + "身為藍軍參謀，接收指揮官意圖並產生 COA 建議。",
        adapter="planner-v1",
        priority=60,
        output_schema_ref="coa_recommendation",
    ),
    Role.INTEL_OFFICER: RoleConfig(
        role=Role.INTEL_OFFICER,
        system_prompt=_PLACEHOLDER + "身為情報官，把零散 detection 融合為敵情判斷並附信心度。",
        adapter="intel-v1",
        priority=40,
        output_schema_ref="base",
    ),
    Role.WHITE_CELL_ASSISTANT: RoleConfig(
        role=Role.WHITE_CELL_ASSISTANT,
        system_prompt=_PLACEHOLDER + "身為統裁輔助，建議注入事件、偵測推演失衡。",
        adapter="whitecell-v1",
        priority=30,
        output_schema_ref="base",
    ),
    Role.AAR_ANALYST: RoleConfig(
        role=Role.AAR_ANALYST,
        system_prompt=_PLACEHOLDER + "身為賽後分析官，從 Ledger 產生敘事與教訓。",
        adapter="aar-v1",
        priority=10,  # 賽後，非即時
        output_schema_ref="base",
    ),
}
