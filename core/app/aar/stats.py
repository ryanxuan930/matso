"""統計儀表板指標（O8.2，SPEC_FULL §14.2）——由 Ledger 事件推導。

純函數。faction 級指標需 unit→faction 對照（由呼叫端提供；缺則只算全域指標）。

## 口徑（WP-D6.2 修正）

三處各自壞掉、且互相掩護的錯誤：分子只認一條路徑寫的鍵、分母把「被物理拒絕」也算成
一次射擊、聚合戰損整包記到守方頭上。三者都只在**讀端**修——寫端（`aggregate.py` 的
`damage_calc`）是 ledger canonical payload 的一部分，動它會改雜湊鏈、讓既有局驗不過。

### 「命中」在三條路徑上不是同一件事（改口徑時查證出來的）

- 單發（`engagement.resolve_engagement`）：擲骰 `roll < p_hit`，是字面的命中。
- 齊射／聯合兵種：走期望值，`HIT` 的定義是**這次交戰造成了戰力損失**（`loss > 0`）。
  於是只要目標在射程內、武器對其裝甲類有殺傷，幾乎必然是 HIT。

所以「命中率」在以齊射/聯合兵種為主的局，實質是**有效交戰比率**而非彈著命中率
（實測既有局：全 COMBINED 的一局為 100%）。這是裁決層的既有語意，本模組只如實反映；
要讓它變回彈著率是裁決層的題目（`adjudication/` 不歸本卡動）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.aar.events import AarEvent
from app.adjudication.engagement import Resolution

_ENGAGE_TYPES = frozenset({"ENGAGEMENT_RESOLVED", "AGGREGATE_ENGAGEMENT_RESOLVED"})

# 個體交戰事件型別——分子/分母只認這一種（聚合交戰是 Lanchester 消耗，沒有命中/失手可言）。
_INDIVIDUAL = "ENGAGEMENT_RESOLVED"

# 統計口徑版本。**改了語意就要 +1**：封存包（`exercise/archive.py`）把算出來的數字
# 寫進歷史演習，舊封存是舊口徑算的，跨版本比較沒有意義。版本號讓「不可比」看得出來，
# 而不是讓人以為紅軍這一季突然變準了。
#   v1（—）：分子只認單發路徑的 `hit` 鍵、分母含 REJECTED、聚合戰損雙側都記在守方。
#   v2（WP-D6.2）：分子改判 `status`、分母排除 REJECTED、聚合戰損雙側分別歸帳。
STATS_VERSION = 2


@dataclass(frozen=True, slots=True)
class AarMetrics:
    total_events: int
    event_counts: dict[str, int]
    engagements: int  # 交戰事件總數（個體＋聚合，含被拒的）——「這場打了幾次」
    attempts: int  # 個體交戰的裁決次數（含 REJECTED）——「下令交火幾次」
    engagements_fired: int  # 其中真的射出去的（attempts 扣掉 REJECTED）——命中率的分母
    hits: int
    hit_rate: float  # hits / engagements_fired
    total_damage: float
    guardrail_blocks: int
    damage_by_faction: dict[str, float]  # 各陣營「承受」的總戰損
    max_tick: int
    stats_version: int = STATS_VERSION


def _resolution(event: AarEvent) -> str | None:
    """事件的裁決結果——**權威來源是 `ai_decision["status"]`**，值域即 `Resolution`。

    寫入端共五處，**全部都寫 `status`**：單發（`engagement.resolve_engagement`）、
    齊射（`engagement._resolve_volley`）、聯合兵種（`combined.resolve_combined_engagement`
    含其 REJECTED 分支）、以及 `adjudicator` 的聚合合法性拒絕與 ROE 拒絕。
    `hit` 布林**只有單發那一條**寫。

    這正是本函式存在的理由：舊版直接讀 `hit`，於是
    「射手建制數 >1 → 齊射」（`engagement.py` 的閘門，幾乎所有班/排/連都成立）與
    「持 ≥2 武器系統 → 聯合兵種」（`adjudicator`）兩條主力路徑的命中一律不計，
    真實推演的命中率恆偏低甚至為 0。

    `hit` 分支只為**舊帳本/封存包**保留（可能寫於 `status` 之前）；兩者並存時以 `status`
    為準——它才是裁決層的輸出，`hit` 是同一件事的衍生副本。
    """
    raw = event.ai_decision.get("status")
    if isinstance(raw, str) and raw:
        return raw.upper()
    if "hit" in event.ai_decision:
        return Resolution.HIT.value if event.ai_decision["hit"] else Resolution.MISS.value
    return None


def _area_losses(event: AarEvent) -> list[tuple[str, float]]:
    """面射擊事件的逐單位戰損（`AREA_FIRE_RESOLVED.losses_by_unit`）。

    ⚠ **只給 AAR 用**：這是 ground truth，不可經 `/aar` 端點下發給參與者
    （`aar/fog.py` 會在投影時把這個鍵剝掉）。統計是在剝掉之前、
    或以全知身分計算的——參與者看到的數字因此本來就會比較少，那是對的。
    """
    raw = event.ai_decision.get("losses_by_unit")
    if not isinstance(raw, dict):
        return []
    return [(str(k), float(v)) for k, v in raw.items() if isinstance(v, (int, float))]


def _aggregate_losses(event: AarEvent) -> list[tuple[str, float]]:
    """聚合交戰的**雙側**戰損（`initiator_loss` / `target_loss` 各歸各的）。

    `aggregate.py` 寫的 `damage_calc = a_loss + b_loss`（雙方相加），而 `target_id`
    只指守方——拿 `damage_calc` 記帳等於把攻擊方的傷亡也算在守方頭上。
    `aar/replay.py` 已為此避開 `damage_calc` fallback（見該檔對「聚合戰損歸帳單側」的註解），
    同一個 bug 在本模組原封不動地存活了下來。

    缺鍵時**不猜**（回空）：寧可少一個數字，也不要給一個把兩方傷亡疊在一起的數字。
    """
    dec = event.ai_decision
    out: list[tuple[str, float]] = []
    for uid, key in ((event.initiator_id, "initiator_loss"), (event.target_id, "target_loss")):
        loss = dec.get(key)
        if uid and isinstance(loss, (int, float)):
            out.append((uid, float(loss)))
    return out


def _charged_losses(event: AarEvent) -> list[tuple[str, float]]:
    """該事件要記進「哪個單位承受了多少戰損」的逐單位清單。

    **單一入口**——三種事件形狀在這裡各走各的，呼叫端只有一條累加迴圈，
    結構上就不可能重複計。`damage_calc` 的語意本來就隨路徑而異：
    - 聚合交戰：雙方損失相加（見 `_aggregate_losses`）
    - 面射擊：沒有單一 `target_id`（打的是座標），逐單位戰損在 `losses_by_unit`
    - 其餘（個體交戰等）：就是目標單側承受的量
    """
    if event.event_type == "AGGREGATE_ENGAGEMENT_RESOLVED":
        return _aggregate_losses(event)
    if event.event_type == "AREA_FIRE_RESOLVED":
        return _area_losses(event)
    dmg = event.damage_calc or 0.0
    return [(event.target_id, dmg)] if dmg and event.target_id else []


def compute_metrics(
    events: Sequence[AarEvent], unit_faction: dict[str, str] | None = None
) -> AarMetrics:
    faction_of = unit_faction or {}
    counts: dict[str, int] = {}
    engagements = attempts = rejected = hits = 0
    total_damage = 0.0
    guardrail_blocks = 0
    damage_by_faction: dict[str, float] = {}
    max_tick = 0

    for e in events:
        counts[e.event_type] = counts.get(e.event_type, 0) + 1
        max_tick = max(max_tick, e.tick)
        if e.event_type in _ENGAGE_TYPES:
            engagements += 1
        if e.event_type == _INDIVIDUAL:
            # ⚠ 這裡也會收到**聚合路徑被拒**的事件：`adjudicator._resolve_aggregate` 的合法性
            # 與 ROE 拒絕寫的是 `ENGAGEMENT_RESOLVED` + `mode: "AGGREGATE"`，成功時才寫
            # `AGGREGATE_ENGAGEMENT_RESOLVED`。所以一個營「打不到」算一次 attempts，
            # 「打到了」卻不算——這個不對稱在寫入端（不歸本卡動），此處如實照收：
            # `attempts` 的語意是「下令交火幾次」，被拒的聚合令確實是其中一次。
            attempts += 1
            # REJECTED＝合法性未過（超射程/無彈/無視線）或規則不准打（ROE/HOLD_FIRE）：
            # **一發都沒射出去**，拿它當命中率分母等於用「想打卻打不到」稀釋火力效益。
            # 兩個數字分開存（attempts / engagements_fired）——一個欄位承載兩種語意，
            # 正是這個 bug 當初的成因。
            status = _resolution(e)
            if status == Resolution.REJECTED.value:
                rejected += 1
            elif status == Resolution.HIT.value:
                hits += 1
        if e.event_type == "GUARDRAIL_INTERVENTION":
            guardrail_blocks += 1
        total_damage += e.damage_calc or 0.0
        for unit_id, loss in _charged_losses(e):
            owner = faction_of.get(unit_id)
            if owner and loss:
                damage_by_faction[owner] = damage_by_faction.get(owner, 0.0) + loss

    fired = attempts - rejected
    return AarMetrics(
        total_events=len(events),
        event_counts=counts,
        engagements=engagements,
        attempts=attempts,
        engagements_fired=fired,
        hits=hits,
        hit_rate=(hits / fired) if fired else 0.0,
        total_damage=round(total_damage, 3),
        guardrail_blocks=guardrail_blocks,
        damage_by_faction={k: round(v, 3) for k, v in damage_by_faction.items()},
        max_tick=max_tick,
    )
