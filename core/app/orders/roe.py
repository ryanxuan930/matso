"""想定交戰規則（ROE）——宣告 → 執行期規則（WP-B6；SPEC_FULL §11.1／§10 G4）。

與 `orders/no_strike.py` 同一套結構紀律：**純函數的解析層**（`parse_roe`，無 DB、無 I/O，
可被裁決層與測試直接用）＋**一個讀 DB 的載入層**（`load_session_roe`）。

## 生效點（只有兩個，且兩個都真的會擋）

1. **裁決層（權威）**：`adjudication/combined.resolve_combined_engagement` 逐武器篩選時，
   被禁的武器不發射、不耗彈、不抽 dispersion——與既有 `fire_policy` 的 HELD 同一條路徑。
   人類令與 AI 令都走這裡，因此**沒有繞過的路徑**。
2. **下令端（早退 + 留痕）**：`orders/precheck` 對「明確指名被禁武器」的 ENGAGE 令直接拒絕
   （`ORDER_NO_STRIKE_ZONE` 之外的另一個 ROE 錯誤碼 `ORDER_ROE_VIOLATION`）。
   只做「明確指名」這一種——沒指名武器的令交由裁決層篩，不在 submit 端猜。

**刻意不做的第三個生效點**：護欄 G4。AI 的 ENGAGE 令幾乎不帶 `weapon_id`（decider 的
輸出指引只提 `fire_policy`），而裁決層已完整覆蓋 AI 路徑；為此在零 DB 的護欄層注入一個
需要查 DB 的武器分類器，換不到任何實際攔截。

## 為何 `reason` 必填
AAR 要能回答「為什麼這場不准用飛彈」。無理由的限制在事後檢討時無法評量——
這與 [JCATS] 式演習的「可評分事件鏈」訴求一致。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

# 「全陣營適用」的保留鍵——想定的 faction id 受 `^[A-Z][A-Z0-9_]{1,31}$` 約束，
# 故 `*` 不可能與真實陣營撞名。
ALL_FACTIONS = "*"

FIRE_POLICIES = frozenset({"FREE", "SMALL_ARMS_ONLY", "ANTI_ARMOR_HOLD"})


@dataclass(frozen=True, slots=True)
class RoeRules:
    """一份已解析的想定 ROE。空實例＝無任何規則（既有想定的語義，零行為變更）。"""

    default_fire_policy: Mapping[str, str] = field(default_factory=dict)
    # faction（或 ALL_FACTIONS）→ 被禁的裝備類別
    forbidden_categories: Mapping[str, frozenset[str]] = field(default_factory=dict)
    # faction（或 ALL_FACTIONS）→ 被禁的裝備範本名稱
    forbidden_templates: Mapping[str, frozenset[str]] = field(default_factory=dict)
    # 逐條限制的理由（供 AAR 與拒絕訊息）：(faction, 被禁項) → reason
    reasons: Mapping[tuple[str, str], str] = field(default_factory=dict)

    @property
    def any_rules(self) -> bool:
        return bool(
            self.default_fire_policy or self.forbidden_categories or self.forbidden_templates
        )

    def fire_policy_for(self, faction: str | None) -> str | None:
        """該陣營的預設火力政策；未宣告 → None（呼叫端維持引擎預設 FREE）。"""
        if faction is None:
            return None
        return self.default_fire_policy.get(faction)

    def forbidden_for(self, faction: str | None) -> frozenset[str]:
        """該陣營被禁的「類別 ∪ 範本名」集合（含全陣營規則）。裁決層以此逐武器比對。"""
        keys = [ALL_FACTIONS] if faction is None else [ALL_FACTIONS, faction]
        out: set[str] = set()
        for key in keys:
            out |= self.forbidden_categories.get(key, frozenset())
            out |= self.forbidden_templates.get(key, frozenset())
        return frozenset(out)

    def reason_for(self, faction: str | None, item: str) -> str:
        """某項限制的理由（找不到 → 空字串）。faction 專屬優先於全陣營。"""
        if faction is not None and (faction, item) in self.reasons:
            return self.reasons[(faction, item)]
        return self.reasons.get((ALL_FACTIONS, item), "")


EMPTY_ROE = RoeRules()


def parse_roe(raw: Any) -> RoeRules:
    """`roe.yaml` 的 dict → `RoeRules`（**純函數**；結構已由 JSON Schema 驗過）。

    非 dict / None → 空規則（無宣告＝無限制，既有想定不受影響）。
    """
    if not isinstance(raw, dict):
        return EMPTY_ROE

    policies: dict[str, str] = {}
    for faction, policy in (raw.get("default_fire_policy") or {}).items():
        if isinstance(policy, str) and policy in FIRE_POLICIES:
            policies[str(faction)] = policy

    categories: dict[str, set[str]] = {}
    templates: dict[str, set[str]] = {}
    reasons: dict[tuple[str, str], str] = {}
    for rule in raw.get("weapon_restrictions") or []:
        if not isinstance(rule, dict):
            continue
        key = str(rule.get("faction") or ALL_FACTIONS)
        reason = str(rule.get("reason") or "")
        for item in rule.get("forbid_categories") or []:
            categories.setdefault(key, set()).add(str(item))
            reasons[(key, str(item))] = reason
        for item in rule.get("forbid_templates") or []:
            templates.setdefault(key, set()).add(str(item))
            reasons[(key, str(item))] = reason

    return RoeRules(
        default_fire_policy=policies,
        forbidden_categories={k: frozenset(v) for k, v in categories.items()},
        forbidden_templates={k: frozenset(v) for k, v in templates.items()},
        reasons=reasons,
    )


def load_session_roe(db: Session, session_id: str) -> RoeRules:
    """讀該局持久化的 ROE 宣告（`WargameSession.roe`）→ `RoeRules`。

    **每次呼叫現讀、不快取**——與 `load_no_strike_cells` 同一理由：白軍可局中修改 ROE
    （SPEC_FULL §12 明列為主席權限），快取會讓變更不生效。規則數是個位數，成本遠低於
    同路徑上的任何一次 DB 查詢。
    """
    from app.models import WargameSession

    row = db.get(WargameSession, session_id)
    return parse_roe(row.roe if row is not None else None)
