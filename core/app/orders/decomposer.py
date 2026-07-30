"""準則分解器（WP-A2）——**純同步純函數**，把一道任務推進一步並吐出低階令。

## 簽名裡看得出來它不可能偷看

```python
def step(mission, state, unit, world_view, *, tick) -> MissionStep
```

`world_view` 是**已經過迷霧投影**的 `build_faction_context()` dict（LLM decider 看的同一份），
`unit` 是其中的一筆己方單位。本模組**不 import `app.models`、不 import `app.state.hot_state`**
——有一條測試釘住這件事。

SPEC_V2 對本卡點名的陷阱是：「分解器讀的 world_view 必須走迷霧投影，否則 AI 經由任務分解
偷看 ground truth，A1 白做」。讓「有沒有偷看」這個問題**看簽名就能回答**，比事後稽核可靠。

地形不走 world_view：地形是公開地理，不是秘密。路徑規劃仍由既有的 `PhysicsGateway`
在預檢時處理——**地形與迷霧一旦共用同一個參數，「這裡有沒有洩漏」就不再是讀簽名能回答的問題**。

## 為什麼是「一次推一步」而不是「一次展開整個計畫」

一次吐出全部子令，等於在還沒接敵的時候就先下好了 ENGAGE ——那些令的目標是分解當下的
contact，而 contact 是會過期的（`IntelContact` 沒有存活性欄位，敵人死了或走了仍留在名單上）。
逐階段展開讓每一步都用**當下**的敵情。

## 兩個刻意的行為

1. **對 contact 下 ENGAGE 是對的，即使那個 contact 是鬼**。`known_enemies` 是最後已知位置、
   永不過期——打一個已經不在那裡的目標正是迷霧下該有的行為。**不可以**為了「修掉」這件事
   去查 DB 核對，那正是陷阱本身。
2. **階段推進只看己方單位狀態，不看「敵人清光了沒」**。理由同上：contact 不會消失，
   以「無敵蹤」當條件的話任務永遠到不了佔領階段。
"""

from __future__ import annotations

from typing import Any

from app.orders.mission import (
    DefendParams,
    MarchParams,
    MissionPayload,
    MissionPhase,
    MissionState,
    MissionStep,
    ScreenParams,
    SeizeParams,
    SubOrder,
    distance_m,
)

# 判定「已抵達」的容差。移動系統走的是逐 tick 推進，落點不會與目標完全重合。
ARRIVAL_TOLERANCE_M = 120.0
# 佔領/防守圈內偵測到敵 contact 時才轉入接戰。半徑由任務參數給，這是沒給時的預設。
DEFAULT_RING_M = 500.0
# 步行機動側寫。分解器**不查單位編裝**（那要 DB）——真正的 profile 由預檢/移動層解析，
# 這裡給的是子令的預設值，與 `orders_bridge` 對 LLM MOVE 令的處理一致。
_DEFAULT_MOBILITY = "FOOT"


def _pos(entity: dict[str, Any]) -> tuple[float, float] | None:
    lat, lng = entity.get("lat"), entity.get("lng")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        return float(lat), float(lng)
    return None


def _move(lat: float, lng: float, reason: str) -> SubOrder:
    """一道精確移動子令。

    `to_h3` 交給落庫端補（它要 h3 套件；本模組刻意不引，見模組說明的純度約束）；
    `to_lat`/`to_lng` 才是實際落點——分解出來的路徑點若被吸附到六角格心，
    沿 axis 的機動會走成鋸齒。
    """
    return SubOrder(
        order_type="MOVE",
        payload={"to_lat": lat, "to_lng": lng, "mobility_profile": _DEFAULT_MOBILITY},
        reason=reason,
    )


def _engage(target_unit_id: str, reason: str) -> SubOrder:
    return SubOrder(order_type="ENGAGE", payload={"target_unit_id": target_unit_id}, reason=reason)


def _posture(value: str, reason: str) -> SubOrder:
    return SubOrder(order_type="POSTURE", payload={"posture": value}, reason=reason)


def _enemies_within(
    world_view: dict[str, Any], lat: float, lng: float, radius_m: float
) -> list[dict[str, Any]]:
    """圈內敵 contact，依距離排序（**排序是決定性的前提**——同一份輸入必得同一串子令）。"""
    out: list[tuple[float, dict[str, Any]]] = []
    for e in world_view.get("known_enemies") or []:
        p = _pos(e)
        if p is None or not e.get("unit_id"):
            continue  # 沒有真實單位 id 的 contact 綁不上 ENGAGE
        d = distance_m(lat, lng, p[0], p[1])
        if d <= radius_m:
            out.append((d, e))
    out.sort(key=lambda t: (t[0], str(t[1].get("unit_id"))))
    return [e for _, e in out]


def _arrived(unit: dict[str, Any], lat: float, lng: float) -> bool:
    p = _pos(unit)
    return p is not None and distance_m(p[0], p[1], lat, lng) <= ARRIVAL_TOLERANCE_M


def _fail(state: MissionState, tick: int, note: str) -> MissionStep:
    return MissionStep(
        state=MissionState(MissionPhase.FAILED, state.waypoint_index, tick), note=note
    )


def _advance(state: MissionState, phase: MissionPhase, tick: int, **kw: int) -> MissionState:
    return MissionState(
        phase=phase, waypoint_index=kw.get("waypoint_index", state.waypoint_index), since_tick=tick
    )


def step(
    mission: MissionPayload,
    state: MissionState,
    unit: dict[str, Any],
    world_view: dict[str, Any],
    *,
    tick: int,
) -> MissionStep:
    """把任務推進一步。回下一個狀態 + 這一步要送出的子令。

    `unit` ＝ `world_view["own_units"]` 裡的那一筆（呼叫端挑好；分解器不做查找，
    因為「挑哪個單位」是 admit 階段的事，而那裡有 DB）。
    """
    if state.phase in (MissionPhase.COMPLETE, MissionPhase.FAILED):
        return MissionStep(state=state)  # 終態不再動
    # 單位沒了/沒位置 → 任務失敗。**這是唯一的失敗來源**：分解器看不到彈藥以外的東西，
    # 「彈盡」由裁決層在子令上表現（子令被拒），不由這裡猜。
    if unit is None or _pos(unit) is None:
        return _fail(state, tick, "單位無位置或已不存在")
    if str(unit.get("status")) == "DESTROYED":
        return _fail(state, tick, "單位已被殲滅")

    params = mission.typed_params()
    if isinstance(params, SeizeParams):
        return _seize(params, state, unit, world_view, tick)
    if isinstance(params, DefendParams):
        return _defend(params, state, unit, world_view, tick)
    if isinstance(params, ScreenParams):
        return _screen(params, state, unit, tick)
    if isinstance(params, MarchParams):
        return _march(params, state, unit, tick)
    return _fail(state, tick, f"未支援的任務型：{mission.mission_type}")


# ---- SEIZE：機動 → 接敵 → 鞏固 → 守 ----


def _seize(
    p: SeizeParams, state: MissionState, unit: dict[str, Any], wv: dict[str, Any], tick: int
) -> MissionStep:
    legs = [*p.axis, p.objective]  # axis 是途經點，objective 永遠是最後一段
    obj = p.objective

    if state.phase is MissionPhase.PLANNED:
        first = legs[0]
        return MissionStep(
            state=_advance(state, MissionPhase.MOVING, tick, waypoint_index=0),
            orders=[_move(first.lat, first.lng, "沿攻擊軸線機動")],
            note="開始機動",
        )

    if state.phase is MissionPhase.MOVING:
        leg = legs[min(state.waypoint_index, len(legs) - 1)]
        # 已在目標圈內且圈內有敵 → 先打，不必等走完最後一段。
        contacts = _enemies_within(wv, obj.lat, obj.lng, p.objective_radius_m)
        if contacts and _within(unit, obj.lat, obj.lng, p.objective_radius_m):
            return MissionStep(
                state=_advance(state, MissionPhase.ENGAGING, tick),
                orders=[_engage(str(contacts[0]["unit_id"]), "目標區內接敵")],
                note="進入接戰",
            )
        if not _arrived(unit, leg.lat, leg.lng):
            return MissionStep(state=state)  # 還在路上，不重下令（去重也會擋，但別依賴它）
        nxt = state.waypoint_index + 1
        if nxt < len(legs):
            leg2 = legs[nxt]
            return MissionStep(
                state=_advance(state, MissionPhase.MOVING, tick, waypoint_index=nxt),
                orders=[_move(leg2.lat, leg2.lng, "續行下一段軸線")],
            )
        # 走到目標了：有敵先打，沒敵直接鞏固。
        if contacts:
            return MissionStep(
                state=_advance(state, MissionPhase.ENGAGING, tick),
                orders=[_engage(str(contacts[0]["unit_id"]), "目標區內接敵")],
                note="進入接戰",
            )
        return MissionStep(
            state=_advance(state, MissionPhase.CONSOLIDATING, tick),
            orders=[_posture("DEFENSE", "佔領後轉守")],
            note="無敵蹤，直接鞏固",
        )

    if state.phase is MissionPhase.ENGAGING:
        contacts = _enemies_within(wv, obj.lat, obj.lng, p.objective_radius_m)
        if contacts:
            return MissionStep(
                state=state, orders=[_engage(str(contacts[0]["unit_id"]), "續行接戰")]
            )
        return MissionStep(
            state=_advance(state, MissionPhase.CONSOLIDATING, tick),
            orders=[_posture("DEFENSE", "佔領後轉守")],
            note="目標區內無可見敵蹤",
        )

    if state.phase is MissionPhase.CONSOLIDATING:
        # **以己方是否在目標圈內判定佔領**，不以「敵人清光了」——contact 永不過期，
        # 用它當條件的話任務永遠到不了這一步（見模組說明第 2 點）。
        if _within(unit, obj.lat, obj.lng, p.objective_radius_m):
            return MissionStep(state=_advance(state, MissionPhase.HOLDING, tick), note="已佔領目標")
        return MissionStep(
            state=_advance(state, MissionPhase.MOVING, tick, waypoint_index=len(legs) - 1),
            orders=[_move(obj.lat, obj.lng, "被推離目標，重新進佔")],
            note="不在目標圈內",
        )

    return MissionStep(state=state)  # HOLDING：穩定狀態，不再下令


def _within(unit: dict[str, Any], lat: float, lng: float, radius_m: float) -> bool:
    p = _pos(unit)
    return p is not None and distance_m(p[0], p[1], lat, lng) <= radius_m


# ---- DEFEND：就位 → 構工 → 守 ----


def _defend(
    p: DefendParams, state: MissionState, unit: dict[str, Any], wv: dict[str, Any], tick: int
) -> MissionStep:
    if state.phase is MissionPhase.PLANNED:
        return MissionStep(
            state=_advance(state, MissionPhase.MOVING, tick),
            orders=[_move(p.area.lat, p.area.lng, "進入防區")],
        )
    if state.phase is MissionPhase.MOVING:
        if not _within(unit, p.area.lat, p.area.lng, p.area_radius_m):
            return MissionStep(state=state)
        return MissionStep(
            state=_advance(state, MissionPhase.CONSOLIDATING, tick),
            orders=[_posture("DEFENSE", "就位構工")],
            note="抵達防區",
        )
    if state.phase is MissionPhase.CONSOLIDATING:
        # 姿態轉換要時間（WP-C1）；**以熱狀態回報的已就位姿態為準**，不自己數 tick。
        if str(unit.get("posture") or "MOVING") == "DEFENSE":
            return MissionStep(state=_advance(state, MissionPhase.HOLDING, tick), note="工事就位")
        return MissionStep(state=state)
    if state.phase is MissionPhase.HOLDING:
        contacts = _enemies_within(wv, p.area.lat, p.area.lng, p.area_radius_m)
        if contacts:
            return MissionStep(
                state=state, orders=[_engage(str(contacts[0]["unit_id"]), "敵進入防區")]
            )
        return MissionStep(state=state)
    return MissionStep(state=state)


# ---- SCREEN：佔位 → 監視（不接戰）----


def _screen(p: ScreenParams, state: MissionState, unit: dict[str, Any], tick: int) -> MissionStep:
    target = p.line[min(state.waypoint_index, len(p.line) - 1)]
    if state.phase is MissionPhase.PLANNED:
        return MissionStep(
            state=_advance(state, MissionPhase.MOVING, tick, waypoint_index=0),
            orders=[_move(target.lat, target.lng, "進入掩護幕位置")],
        )
    if state.phase is MissionPhase.MOVING:
        if not _arrived(unit, target.lat, target.lng):
            return MissionStep(state=state)
        return MissionStep(state=_advance(state, MissionPhase.HOLDING, tick), note="掩護幕就位")
    # HOLDING：**刻意不下任何 ENGAGE**——掩護幕的任務是偵測回報，不是接戰。
    # 敵情本身經偵測子系統進 contact，不需要分解器做任何事。
    return MissionStep(state=state)


# ---- MOVE_MARCH：按序通過航路點 ----


def _march(p: MarchParams, state: MissionState, unit: dict[str, Any], tick: int) -> MissionStep:
    if state.phase is MissionPhase.PLANNED:
        first = p.route[0]
        return MissionStep(
            state=_advance(state, MissionPhase.MOVING, tick, waypoint_index=0),
            orders=[_move(first.lat, first.lng, "行軍：第 1 航路點")],
        )
    if state.phase is MissionPhase.MOVING:
        cur = p.route[min(state.waypoint_index, len(p.route) - 1)]
        if not _arrived(unit, cur.lat, cur.lng):
            return MissionStep(state=state)
        nxt = state.waypoint_index + 1
        if nxt >= len(p.route):
            return MissionStep(state=_advance(state, MissionPhase.COMPLETE, tick), note="行軍完成")
        point = p.route[nxt]
        return MissionStep(
            state=_advance(state, MissionPhase.MOVING, tick, waypoint_index=nxt),
            orders=[_move(point.lat, point.lng, f"行軍：第 {nxt + 1} 航路點")],
        )
    return MissionStep(state=state)


__all__ = ["ARRIVAL_TOLERANCE_M", "DEFAULT_RING_M", "step"]
