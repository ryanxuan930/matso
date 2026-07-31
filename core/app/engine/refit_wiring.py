"""修復與人員補充（WP-C7.3）。

[JCATS-A p.26–27]：**「絕非申請後直接恢復戰力」**——補給/修復/人員補充需合理作業時間，
且要「**於後方恢復再前送**」。這張卡的整個重點就是那兩句話：整補要花時間，而且不能在前線做。

## 三個前提，缺一不整補

1. **在補給點半徑內**（C7.2 的 `nearest_usable`）——後方，不是隨便哪裡。
2. **附近沒有敵軍**（`SAFE_DISTANCE_M`）——落實「前線不整補」。
3. **有 Class IX**（維修件）——修復要料。

## 中斷：被打就停

驗收條文寫「修復中的單位遭襲即中斷整補」。判定用**壓制度**——被射擊就會累積壓制
（WP-C1），那是「遭襲」最直接、而且已經存在的訊號。用「戰力下降」判會慢一拍
（要真的被打掉人才算），而整補該在第一發子彈打過來時就停。

## 敵軍距離用的是真值，那是刻意的

紅線 3 管的是**玩家**的迷霧，不是物理。一支部隊知不知道「附近有敵人」不需要它先偵測到
——槍聲、車聲、上級通報都算。而且這條規則的效果是**限制自己**（不准整補），
不是給予資訊優勢；`REFIT_BLOCKED` 事件也只說「附近有敵軍」不說是誰、在哪。

## 修復是逐 tick 累積，不是一次到位

`REPAIR_PER_DAY` 是每模擬日恢復的戰力點。與補給消耗同一套「按經過 tick 補算」的算法
——每 tick 加一點會累積浮點誤差，而且回滾之後對不起來。
"""

from __future__ import annotations

from typing import Any

from app.adjudication.supply import SupplyClass, SupplyLevel
from app.engine.supply_wiring import SUPPLY_KEY, read_levels, write_levels
from app.engine.suppression_wiring import SUPPRESSION_KEY

REFIT_TICK_KEY = "refit_tick"

# 前線不整補：這個半徑內有敵軍就不觸發（[JCATS-A p.27]）。
SAFE_DISTANCE_M = 5000.0

# 每模擬日恢復的戰力點（2026-07-31 校準）。
#
# **錨點：一個戰力掉到 60%（損失 40 點）的裝甲連退到後方整補，約 4 個模擬日恢復滿編。**
# 40 ÷ 4 ＝ 10。⚠ **這是假設，不是量測**——它要釘住的是 [JCATS-A p.26–27]
# 「絕非申請後直接恢復戰力／於後方恢復再前送」講的**量級**：整補以「日」計，不是以分鐘計。
#
# 等比例調整：恢復量與本值**嚴格線性**（`gained = per_day × days`），
# 想把「4 天」改成「2 天」就 ×2，不必動其他任何常數。
#
# ⚠ 契約把它定義成**絕對戰力點**而不是建制比例（`core_api.yaml`）。實務上
# `authorized_strength` 幾乎恆為 100（DB 預設，想定 loader 不寫），所以 10 點/日
# ≈ 10% 建制/日；日後真的出現非 100 的編制時，小單位會顯得修得比較快。
REPAIR_PER_DAY = 10.0

# 每恢復一點戰力消耗的 Class IX。改它要連 `supply.DAILY_CONSUMPTION[IX]` 一起等比例調
# ——那個值的錨點就是「日常保養 ＝ 每日修回 1 點戰力的料」。
PARTS_PER_POINT = 0.5

_MS_PER_DAY = 86_400_000


def is_under_attack(state: dict[str, Any]) -> bool:
    """遭襲中？——用**壓制度**判定（WP-C1：被射擊就累積壓制）。

    用「戰力下降」判會慢一拍（要真的被打掉人才算），而整補該在第一發子彈打過來時就停。
    """
    raw = state.get(SUPPRESSION_KEY)
    return isinstance(raw, (int, float)) and raw > 0


def enemy_near(
    hot: Any, faction_of: Any, own_faction: str, lat: float, lng: float, radius_m: float
) -> bool:
    """半徑內有沒有敵軍（**真值**）。

    ⚠ 紅線 3 管的是玩家的迷霧，不是物理。一支部隊知不知道「附近有敵人」不需要它先偵測到
    ——槍聲、車聲、上級通報都算。而且這條規則的效果是**限制自己**（不准整補），
    不是給予資訊優勢。
    """
    from app.movement.attrition import haversine_m

    for unit_id, state in hot.get_all().items():
        other = faction_of(unit_id)
        if not other or other == own_faction:
            continue
        try:
            other_lat, other_lng = float(state["lat"]), float(state["lng"])
        except (KeyError, TypeError, ValueError):
            continue
        if haversine_m((lng, lat), (other_lng, other_lat)) <= radius_m:
            return True
    return False


def refit_tick(
    db: Any,
    hot: Any,
    session_id: str,
    unit_lookup: Any,
    faction_of: Any,
    tick: int,
    tick_rate_ms: int,
    repair_per_day: float = REPAIR_PER_DAY,
) -> list[Any]:
    """整補結算（WP-C7.3）。回帳本事件。

    `repair_per_day <= 0` → **立刻回空 list**，一次查詢都不做。但預設已不再是 0，
    所以既有局的零成本保證改由 `_is_refit_candidate` 撐住（見該函式）。
    """
    from app.engine.supply_points import load_points
    from app.state.ledger import LedgerEvent

    if repair_per_day <= 0:
        return []
    damaged = [uid for uid in sorted(hot.get_all()) if _is_refit_candidate(hot.get_unit(uid) or {})]
    if not damaged:
        return []
    points = load_points(db, session_id)
    if not points:
        return []

    events: list[Any] = []
    for unit_id in damaged:
        state = hot.get_unit(unit_id) or {}
        meta = unit_lookup(unit_id)
        if meta is None:
            continue
        faction, lat, lng = meta
        blocked = _blocked_reason(hot, state, faction_of, faction, lat, lng, points)
        if blocked is not None:
            # 只在**狀態改變時**發事件（進入受阻），否則每 tick 都會洗版。
            if state.get(REFIT_TICK_KEY) is not None:
                hot.update_unit(unit_id, {REFIT_TICK_KEY: None})
                events.append(
                    LedgerEvent(
                        event_type="REFIT_BLOCKED",
                        tick=tick,
                        initiator_id=unit_id,
                        ai_decision={"reason": blocked},
                    )
                )
            continue
        events.extend(_apply_repair(db, hot, unit_id, state, tick, tick_rate_ms, repair_per_day))
    return events


def _missing_strength(state: dict[str, Any]) -> float:
    """還差多少戰力才滿編。**四捨五入到熱狀態的三位小數解析度**——差額比解析度還小就是滿編了，
    否則 `_apply_repair` 會為一個永遠寫不進去的零頭無限期地掛在整補候選名單上。"""
    auth = state.get("authorized_strength")
    cur = state.get("strength")
    if not isinstance(auth, (int, float)) or not isinstance(cur, (int, float)):
        return 0.0
    return max(0.0, round(float(auth) - float(cur), 3))


def _is_refit_candidate(state: dict[str, Any]) -> bool:
    """值得為它查一次 DB 的單位：有戰損，**且編制了 Class IX 或本來就在整補中**。

    Class IX 本來就是 `_blocked_reason` 的硬前提，提到這裡篩是為了讓「既有局零成本」
    是**結構性**的、而不是靠「剛好這一局沒有補給點」：沒有 `supply` 鍵 → `read_levels`
    回空 dict → 這裡就 False → **一次 DB 查詢都不發**。`REPAIR_PER_DAY` 從 0 變成真的
    數字之後，這是唯一還撐得住那個保證的地方。

    「本來就在整補中」那一支不能省——少了它，編制被改掉的單位會留著一個永遠不歸零的
    `refit_tick`，等於中斷了卻還在計時。
    """
    if _missing_strength(state) <= 0:
        return False
    parts = read_levels(state).get(SupplyClass.IX)
    return (parts is not None and parts.declared) or state.get(REFIT_TICK_KEY) is not None


def _blocked_reason(
    hot: Any,
    state: dict[str, Any],
    faction_of: Any,
    faction: str,
    lat: float,
    lng: float,
    points: list[Any],
) -> str | None:
    """三個前提，缺一不整補。回受阻原因；可以整補回 None。"""
    from app.engine.supply_points import nearest_usable

    if is_under_attack(state):
        return "UNDER_ATTACK"
    if nearest_usable(points, faction, lat, lng) is None:
        return "NO_SUPPLY_POINT"
    if enemy_near(hot, faction_of, faction, lat, lng, SAFE_DISTANCE_M):
        return "ENEMY_NEAR"  # 前線不整補（[JCATS-A p.27]）
    levels = read_levels(state)
    parts = levels.get(SupplyClass.IX)
    if parts is None or not parts.declared or parts.on_hand <= 0:
        return "NO_PARTS"
    return None


def _apply_repair(
    db: Any,
    hot: Any,
    unit_id: str,
    state: dict[str, Any],
    tick: int,
    tick_rate_ms: int,
    per_day: float,
) -> list[Any]:
    from app.adjudication.effectiveness import effectiveness_pct
    from app.models.tables import TacticalUnit
    from app.state.ledger import LedgerEvent

    raw_last = state.get(REFIT_TICK_KEY)
    if not isinstance(raw_last, (int, float)):
        # **第一個 tick 只開始計時，不修**——「絕非申請後直接恢復戰力」。
        hot.update_unit(unit_id, {REFIT_TICK_KEY: tick})
        return [LedgerEvent(event_type="REFIT_STARTED", tick=tick, initiator_id=unit_id)]
    elapsed = max(0, tick - int(raw_last))
    if elapsed <= 0:
        return []
    days = elapsed * tick_rate_ms / _MS_PER_DAY
    levels = read_levels(state)
    parts = levels.get(SupplyClass.IX, SupplyLevel())
    wanted = per_day * days
    # 料件是硬上限：有多少料修多少。
    affordable = parts.on_hand / PARTS_PER_POINT if PARTS_PER_POINT > 0 else wanted
    gained = min(wanted, affordable, _missing_strength(state))
    if gained <= 0:
        return []
    strength = float(state.get("strength") or 0.0) + gained
    auth = float(state.get("authorized_strength") or 100.0) or 100.0
    health = effectiveness_pct(strength / auth)
    levels[SupplyClass.IX] = SupplyLevel(
        max(0.0, parts.on_hand - gained * PARTS_PER_POINT), parts.capacity
    )
    # **DB 也要寫**，與交戰／移動／火力那三個寫入端一致。
    # 只寫熱狀態的話，`GET /units` 讀的是 `TacticalUnit.current_strength`（DB），
    # 於是整補完成後**單位清單與資訊卡上的戰力不會變**，要等下一次 checkpoint
    # （預設 600 tick）才追上——操作員把部隊撤回後方修了一整天，畫面說它還是殘破的。
    unit = db.get(TacticalUnit, unit_id)
    if unit is not None:
        unit.current_strength = strength
        unit.health_status = health
    hot.update_unit(
        unit_id,
        {
            # ⚠ **不四捨五入**（曾經是三位小數）。這是逐次結算累加出來的量，捨到千分位等於
            # 每次結算丟一個零頭：1 秒/tick 的想定每 tick 只修 1.2e-4 點，捨掉就永遠不動、
            # 進位就多修七成，而 `REFIT_PROGRESS` 事件與畫面都看不出異常。
            # 熱狀態的 `strength` 在交戰/移動/火力那三個寫入端本來就都是直接寫浮點。
            "strength": strength,
            "health": health,
            SUPPLY_KEY: write_levels(levels),
            REFIT_TICK_KEY: tick,
        },
    )
    return [
        LedgerEvent(
            event_type="REFIT_PROGRESS",
            tick=tick,
            initiator_id=unit_id,
            ai_decision={"restored": round(gained, 3), "strength_after": round(strength, 3)},
        )
    ]


__all__ = [
    "PARTS_PER_POINT",
    "REFIT_TICK_KEY",
    "REPAIR_PER_DAY",
    "SAFE_DISTANCE_M",
    "enemy_near",
    "is_under_attack",
    "refit_tick",
]
