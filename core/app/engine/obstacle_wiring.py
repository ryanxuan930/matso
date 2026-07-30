"""障礙與工兵在活執行期的接線（WP-C2）。

`adjudication/obstacles.py` 是純函數；本模組只做 I/O 邊界：把 `MapFeature.attributes`
翻成型別、擲觸雷、記破障進度、執行 ENGINEER 令。

## 中性保證是**結構性**的，不是靠測試看著

`typed(...)` 把沒有 `attributes.obstacle_type` 的標註整個濾掉。既有局的每一片障礙都沒有
那個屬性 → 逐 tick 這條路徑拿到空 list → 一次幾何判定都不做、一次 RNG 都不抽 → 位元不變。

⚠ WP-C3 就是在這一層栽的：`mounted` 缺鍵被 `bool()` 收成 False，讓既有局命中率無聲掉 20%。
純函數的預設參數天生中性，**會出事的永遠是「接線怎麼把缺值翻成值」**。所以濾在入口。

## 「是不是工兵」為什麼會有兩個鍵

本模組原本只認 `attributes.unit_kind`，理由是「為一個布林開 migration 換不到查詢能力」。
**那個理由現在過期了**：2026-07-30 的兵科卡加了真正的 `TacticalUnit.branch` 欄位
（想定編輯器與 ORBAT PATCH 都寫它），於是變成**兩個互不相通的鍵**——
ORBAT 寫 `branch=ENGINEER`，這裡卻讀 `attributes.unit_kind`，
結果是 **ENGINEER 令永遠過不了預檢**（「障礙作業需工兵單位」），
而畫面上那支部隊明明標著工兵符號。

現在兩個都認：`branch` 是正途，`attributes.unit_kind` 保留為既有資料的退路。
缺值＝不是工兵仍然是安全的方向：**多算成工兵才會讓雷區失效**。
"""

from __future__ import annotations

from typing import Any

from app.adjudication.obstacles import (
    MINE_STRIKE_SUPPRESSION,
    ObstacleType,
    blocks_road,
    mine_strike_probability,
    obstacle_type_of,
    speed_multiplier,
)
from app.adjudication.suppression import MAX_SUPPRESSION
from app.engine.rng import DeterministicRNG
from app.engine.suppression_wiring import SUPPRESSION_KEY
from app.movement.attrition import Obstacle
from app.state.hot_state import HotStateStore
from app.state.ledger import LedgerEvent

UNIT_KIND_KEY = "unit_kind"
ENGINEER_KIND = "ENGINEER"
BREACH_KEY = "breach"  # 熱狀態：{"feature_id": str, "ticks_left": int}


def is_engineer(unit: Any) -> bool:
    """這支部隊是不是工兵。**`branch` 與 `attributes.unit_kind` 兩個鍵都認**。

    傳入 `TacticalUnit`（或任何有 `.branch` / `.attributes` 的物件）。
    也容忍直接傳 attributes dict——舊呼叫端的相容路徑。
    """
    branch = getattr(unit, "branch", None)
    if branch is not None:
        value = getattr(branch, "value", branch)
        if str(value).upper() == ENGINEER_KIND:
            return True
    attributes = getattr(unit, "attributes", unit)
    if not isinstance(attributes, dict):
        return False
    return str(attributes.get(UNIT_KIND_KEY) or "").upper() == ENGINEER_KIND


def typed(obstacles: list[Obstacle]) -> list[Obstacle]:
    """只留下有宣告型別的障礙——WP-C2 的逐 tick 裁決只對這些生效。

    這是中性保證的**結構**（見模組說明）：既有標註在這裡就被濾掉了。
    """
    return [o for o in obstacles if obstacle_type_of(o.obstacle_type) is not None]


def transit_speed_multiplier(here: list[Obstacle], *, engineer: bool) -> float:
    """站在這些障礙裡的速度倍率。多重障礙 → **取最嚴格的那個**。

    相乘會讓「雷區＋鐵絲網」比兩者之和還難走一個數量級；現實裡疊障礙的效果是
    「以最難的那道為準」，不是連乘。
    """
    if not here:
        return 1.0
    return min(
        speed_multiplier(
            obstacle_type_of(o.obstacle_type), is_engineer=engineer, breached=o.breached
        )
        for o in here
    )


def road_is_cut(here: list[Obstacle]) -> bool:
    """這些障礙裡有沒有未修復的斷橋——有的話**道路加速失效**。

    斷橋不「減速」（`speed_multiplier` 對它是中性 1.0）：炸斷的橋不會讓你走得慢，
    它讓你**不能再沿著路走**，得繞路或涉水。那是路徑層的效果，不是通過倍率。
    這也是為什麼 `blocks_road()` 早就寫好卻一直沒有消費者——它要接的地方在
    `movement` 的道路加速分支，不在障礙倍率那一段。

    已破障（工兵架好便橋）→ 不再阻斷。
    """
    return any(blocks_road(obstacle_type_of(o.obstacle_type), breached=o.breached) for o in here)


def roll_mine_strike(
    here: list[Obstacle],
    distance_km: float,
    rng: DeterministicRNG,
    *,
    engineer: bool,
    p_per_km: float | None = None,
    engineer_mult: float | None = None,
) -> Obstacle | None:
    """本 tick 是否觸雷。回踩到的那一片雷區，沒觸雷回 None。

    **每片雷區各擲一次**（站在兩片重疊雷區裡本來就更危險），但一觸即回——
    一個 tick 只處理一次觸雷事件。

    ⚠ 只在 `here` 有雷區時才動 RNG。`rng` 是有狀態的串流，多抽一次會讓**後面所有**
    的隨機結果位移——既有局（無型別障礙 → `here` 空）因此一次都不會抽到。
    """
    for o in here:
        otype = obstacle_type_of(o.obstacle_type)
        if otype is not ObstacleType.MINEFIELD:
            continue
        p = mine_strike_probability(
            otype,
            distance_km,
            is_engineer=engineer,
            breached=o.breached,
            density=o.density,
            p_per_km=p_per_km,
            engineer_mult=engineer_mult,
        )
        if p <= 0.0:
            continue
        if rng.random() < p:
            return o
    return None


def apply_mine_suppression(hot: HotStateStore, unit_id: str) -> float:
    """觸雷 → 壓制。回新值。

    走的是 `MAX_SUPPRESSION` 的同一個上限，但**不經過武器類別表**——踩到雷不是被誰射中，
    沒有對應的武器類別可查。這個常數是獨立校準的（見 `MINE_STRIKE_SUPPRESSION`）。
    """
    state = hot.get_unit(unit_id) or {}
    raw = state.get(SUPPRESSION_KEY, 0.0)
    current = float(raw) if isinstance(raw, (int, float)) else 0.0
    value = min(MAX_SUPPRESSION, max(0.0, current) + MINE_STRIKE_SUPPRESSION)
    hot.update_unit(unit_id, {SUPPRESSION_KEY: round(value, 3)})
    return value


def drain_engineer_orders(db: Any, session_id: str, tick: int) -> list[LedgerEvent]:
    """執行 ENGINEER 令（WP-C2）。回本 tick 產生的帳本事件。

    ## 為什麼跟 POSTURE/FORMATION 不同形狀

    那兩個是**宣告**——一個 tick 之內完成。障礙作業是**工作**：破一片雷區 45 分鐘、
    炸一座橋 2 小時。所以令收下後停在 EXECUTING，把完工 tick 記在 payload 的
    `_work_until_tick`（與 MOVE 的 `_leg` 同一套：進度住在令上，checkpoint 自動涵蓋）。

    ⚠ 完工那一刻才改 `MapFeature`。中途取消（令被刪）＝白做，這是對的：
    破到一半的雷區還是雷區。
    """
    from sqlalchemy import select

    from app.adjudication.obstacles import breach_ticks, obstacle_type_of
    from app.models.enums import OrderStatus
    from app.models.tables import MapFeature, Order, TacticalUnit
    from app.orders.state_machine import next_status

    orders = db.scalars(
        select(Order)
        .where(
            Order.session_id == session_id,
            Order.status.in_([OrderStatus.VALIDATED, OrderStatus.EXECUTING]),
            Order.order_type == "ENGINEER",
        )
        .order_by(Order.issued_at_tick, Order.id)
    ).all()
    events: list[LedgerEvent] = []
    for order in orders:
        payload = dict(order.payload or {})
        action = str(payload.get("action") or "")
        otype = obstacle_type_of(payload.get("obstacle_type"))
        if order.status == OrderStatus.VALIDATED:
            order.status = next_status(order.status, OrderStatus.EXECUTING)
            # BREACH 的工時看**標的**的型別；EMPLACE 看要設的型別。
            work = breach_ticks(otype if action == "EMPLACE" else _target_type(db, payload))
            payload["_work_until_tick"] = tick + work
            order.payload = payload
            events.append(
                LedgerEvent(
                    event_type="ENGINEER_WORK_STARTED",
                    tick=tick,
                    initiator_id=order.unit_id,
                    detail={
                        "order_id": order.id,
                        "action": action,
                        "eta_tick": tick + work,
                        "work_ticks": work,
                    },
                )
            )
            continue
        until = payload.get("_work_until_tick")
        if not isinstance(until, (int, float)) or tick < int(until):
            continue  # 還在施工
        unit = db.get(TacticalUnit, order.unit_id)
        if action == "BREACH":
            feature = db.get(MapFeature, str(payload.get("feature_id") or ""))
            if feature is None or feature.session_id != session_id:
                # 標的在施工期間被刪了。狀態機沒有 FAILED，用 CANCELLED——
                # **不可以判 COMPLETED**：那會讓 AAR 看起來像「破障成功」。
                order.status = next_status(order.status, OrderStatus.CANCELLED)
                order.resolved_at_tick = tick
                events.append(
                    LedgerEvent(
                        event_type="ENGINEER_WORK_ABORTED",
                        tick=tick,
                        initiator_id=order.unit_id,
                        detail={"order_id": order.id, "reason": "TARGET_GONE"},
                    )
                )
                continue
            # ⚠ JSON 欄位要**整包換掉**才會被 SQLAlchemy 視為 dirty；原地 mutate 不會落庫。
            feature.attributes = {**(feature.attributes or {}), "breached": True}
            detail = {"order_id": order.id, "feature_id": feature.id, "label": feature.label}
            event_type = "OBSTACLE_BREACHED"
        else:
            feature = MapFeature(
                session_id=session_id,
                kind="OBSTACLE",
                geometry_type="POINT",
                geometry=[float(payload.get("lng") or 0.0), float(payload.get("lat") or 0.0)],
                owner_faction=(unit.faction if unit is not None else ""),
                label=f"{otype.value if otype else 'OBSTACLE'}",
                influence_radius_m=float(payload.get("radius_m") or 200.0),
                attributes={"obstacle_type": otype.value if otype else None},
            )
            db.add(feature)
            db.flush()
            detail = {
                "order_id": order.id,
                "feature_id": feature.id,
                "obstacle_type": otype.value if otype else None,
            }
            event_type = "OBSTACLE_EMPLACED"
        order.status = next_status(order.status, OrderStatus.COMPLETED)
        order.resolved_at_tick = tick
        events.append(
            LedgerEvent(event_type=event_type, tick=tick, initiator_id=order.unit_id, detail=detail)
        )
    db.commit()
    return events


def _target_type(db: Any, payload: dict[str, Any]) -> Any:
    """BREACH 標的的障礙型別（決定工時）。查不到 → None → 工時 0（無事可破，下個 tick 就完工）。"""
    from app.adjudication.obstacles import obstacle_type_of
    from app.models.tables import MapFeature

    feature = db.get(MapFeature, str(payload.get("feature_id") or ""))
    if feature is None:
        return None
    attrs = feature.attributes if isinstance(feature.attributes, dict) else {}
    return obstacle_type_of(attrs.get("obstacle_type"))


__all__ = [
    "BREACH_KEY",
    "ENGINEER_KIND",
    "UNIT_KIND_KEY",
    "apply_mine_suppression",
    "drain_engineer_orders",
    "is_engineer",
    "road_is_cut",
    "roll_mine_strike",
    "transit_speed_multiplier",
    "typed",
]
