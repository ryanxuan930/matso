"""準則分解器（WP-A2 卡 1）——四種任務型的分解快照 + 純度約束。

SPEC_V2 對本卡點名的陷阱：「分解器讀的 world_view 必須走迷霧投影，否則 AI 經由任務分解
偷看 ground truth，A1 白做」。本檔的第一組測試把那件事變成**讀簽名就能回答**的問題。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.orders.decomposer import ARRIVAL_TOLERANCE_M, step
from app.orders.mission import (
    MissionPayload,
    MissionPhase,
    MissionState,
    MissionType,
)

_OBJ = {"lat": 24.0, "lng": 121.0}


def _unit(lat: float, lng: float, **kw: object) -> dict:  # type: ignore[type-arg]
    return {"unit_id": "b1", "designation": "藍1", "lat": lat, "lng": lng, **kw}


def _wv(enemies: list[dict] | None = None) -> dict:  # type: ignore[type-arg]
    return {"own_units": [], "known_enemies": enemies or []}


def _mission(mtype: MissionType, **params: object) -> MissionPayload:
    return MissionPayload(mission_type=mtype, params=params)


def _run(mission: MissionPayload, unit: dict, wv: dict, state: MissionState, tick: int = 1):  # type: ignore[type-arg,no-untyped-def]
    return step(mission, state, unit, wv, tick=tick)


# ---- 純度：迷霧陷阱要能靠讀簽名回答 ----


def test_decomposer_imports_nothing_that_could_see_ground_truth() -> None:
    """**本檔最重要的一條**。

    分解器只要 import 了 `app.models`（DB）或 `app.state.hot_state`（全局熱狀態），
    它就有能力繞過迷霧投影去看真相——而那正是 SPEC_V2 對本卡點名的陷阱。
    把它做成靜態約束，就不必靠每次 review 有人記得check。
    """
    src = pathlib.Path(__file__).resolve().parents[2] / "app" / "orders" / "decomposer.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    forbidden = {m for m in modules if m.startswith(("app.models", "app.state", "sqlalchemy"))}
    assert not forbidden, f"分解器不得接觸真相來源：{sorted(forbidden)}"
    # 白名單：只准 typing 與自己的純資料模組。
    assert modules <= {"__future__", "typing", "app.orders.mission"}, sorted(modules)


def test_step_is_a_pure_function_of_its_arguments() -> None:
    """同一份輸入必得同一份輸出——決定性是重播的前提（紅線 1）。"""
    m = _mission(MissionType.SEIZE, objective=_OBJ)
    unit = _unit(24.02, 121.0)
    wv = _wv()
    a = _run(m, unit, wv, MissionState())
    b = _run(m, unit, wv, MissionState())
    assert a.state == b.state
    assert [(o.order_type, o.payload) for o in a.orders] == [
        (o.order_type, o.payload) for o in b.orders
    ]


def test_engage_target_order_is_deterministic_by_distance_then_id() -> None:
    """多個 contact 時挑哪一個必須穩定，否則同一局重播會下不同的令。"""
    m = _mission(MissionType.SEIZE, objective=_OBJ, objective_radius_m=5000)
    unit = _unit(24.0, 121.0)
    far = {"unit_id": "r-far", "lat": 24.01, "lng": 121.0}
    near = {"unit_id": "r-near", "lat": 24.001, "lng": 121.0}
    out = _run(m, unit, _wv([far, near]), MissionState(MissionPhase.ENGAGING))
    assert out.orders[0].payload["target_unit_id"] == "r-near"
    # 順序顛倒不改變結果。
    out2 = _run(m, unit, _wv([near, far]), MissionState(MissionPhase.ENGAGING))
    assert out2.orders[0].payload["target_unit_id"] == "r-near"


# ---- SEIZE ----


def test_seize_walks_the_axis_then_consolidates() -> None:
    """驗收條文的主線：機動 → （無敵）→ 佔領 → 轉守。"""
    axis = [{"lat": 24.05, "lng": 121.0}]
    m = _mission(MissionType.SEIZE, objective=_OBJ, axis=axis, objective_radius_m=300)

    # PLANNED → 先走軸線第一段
    s0 = _run(m, _unit(24.10, 121.0), _wv(), MissionState())
    assert s0.state.phase is MissionPhase.MOVING
    assert s0.orders[0].order_type == "MOVE"
    assert s0.orders[0].payload["to_lat"] == pytest.approx(24.05)

    # 還在路上 → 不重下令
    mid = _run(m, _unit(24.08, 121.0), _wv(), s0.state)
    assert mid.orders == [] and mid.state.phase is MissionPhase.MOVING

    # 抵達軸線點 → 續行目標
    at_axis = _run(m, _unit(24.05, 121.0), _wv(), s0.state)
    assert at_axis.state.waypoint_index == 1
    assert at_axis.orders[0].payload["to_lat"] == pytest.approx(24.0)

    # 抵達目標且無敵 → 直接鞏固並轉守
    at_obj = _run(m, _unit(24.0, 121.0), _wv(), at_axis.state)
    assert at_obj.state.phase is MissionPhase.CONSOLIDATING
    assert (at_obj.orders[0].order_type, at_obj.orders[0].payload) == (
        "POSTURE",
        {"posture": "DEFENSE"},
    )

    # 在目標圈內 → 佔領完成
    held = _run(m, _unit(24.0, 121.0), _wv(), at_obj.state)
    assert held.state.phase is MissionPhase.HOLDING
    assert held.orders == []


def test_seize_engages_before_consolidating() -> None:
    m = _mission(MissionType.SEIZE, objective=_OBJ, objective_radius_m=1000)
    enemy = {"unit_id": "r1", "lat": 24.001, "lng": 121.0}
    moving = MissionState(MissionPhase.MOVING)
    out = _run(m, _unit(24.0, 121.0), _wv([enemy]), moving)
    assert out.state.phase is MissionPhase.ENGAGING
    assert out.orders[0].payload == {"target_unit_id": "r1"}


def test_seize_engaging_ends_when_no_visible_contact_remains() -> None:
    m = _mission(MissionType.SEIZE, objective=_OBJ, objective_radius_m=1000)
    out = _run(m, _unit(24.0, 121.0), _wv(), MissionState(MissionPhase.ENGAGING))
    assert out.state.phase is MissionPhase.CONSOLIDATING
    assert out.orders[0].order_type == "POSTURE"


def test_seize_reoccupies_if_pushed_off_the_objective() -> None:
    """被推離目標 → 回到 MOVING 重新進佔，而不是留在鞏固階段假裝佔著。"""
    m = _mission(MissionType.SEIZE, objective=_OBJ, objective_radius_m=200)
    out = _run(m, _unit(24.05, 121.0), _wv(), MissionState(MissionPhase.CONSOLIDATING))
    assert out.state.phase is MissionPhase.MOVING
    assert out.orders[0].order_type == "MOVE"


def test_seize_phase_does_not_depend_on_enemies_being_gone() -> None:
    """**contact 永不過期**（`IntelContact` 沒有存活性欄位）——以「無敵蹤」當佔領條件
    會讓任務永遠到不了 HOLDING。這裡確認即使圈內還掛著鬼 contact，佔領照樣成立。"""
    m = _mission(MissionType.SEIZE, objective=_OBJ, objective_radius_m=1000)
    ghost = {"unit_id": "r-ghost", "lat": 24.001, "lng": 121.0}
    out = _run(m, _unit(24.0, 121.0), _wv([ghost]), MissionState(MissionPhase.CONSOLIDATING))
    assert out.state.phase is MissionPhase.HOLDING


# ---- DEFEND ----


def test_defend_moves_then_digs_in_then_holds() -> None:
    m = _mission(MissionType.DEFEND, area=_OBJ, area_radius_m=300)
    s0 = _run(m, _unit(24.05, 121.0), _wv(), MissionState())
    assert s0.state.phase is MissionPhase.MOVING and s0.orders[0].order_type == "MOVE"

    arrived = _run(m, _unit(24.0, 121.0), _wv(), s0.state)
    assert arrived.state.phase is MissionPhase.CONSOLIDATING
    assert arrived.orders[0].payload == {"posture": "DEFENSE"}

    # 姿態轉換要時間（WP-C1）——**以熱狀態回報的已就位姿態為準**，不自己數 tick。
    digging = _run(m, _unit(24.0, 121.0, posture="MOVING"), _wv(), arrived.state)
    assert digging.state.phase is MissionPhase.CONSOLIDATING

    settled = _run(m, _unit(24.0, 121.0, posture="DEFENSE"), _wv(), arrived.state)
    assert settled.state.phase is MissionPhase.HOLDING


def test_defend_engages_intruders_while_holding() -> None:
    m = _mission(MissionType.DEFEND, area=_OBJ, area_radius_m=1000)
    enemy = {"unit_id": "r1", "lat": 24.002, "lng": 121.0}
    out = _run(m, _unit(24.0, 121.0), _wv([enemy]), MissionState(MissionPhase.HOLDING))
    assert out.orders[0].payload == {"target_unit_id": "r1"}
    assert out.state.phase is MissionPhase.HOLDING  # 守備不因接戰而換階段


# ---- SCREEN ----


def test_screen_takes_position_and_never_engages() -> None:
    """**掩護幕的任務是偵測回報，不是接戰**——這條釘住「圈內有敵也不下 ENGAGE」。"""
    line = [{"lat": 24.0, "lng": 121.0}, {"lat": 24.0, "lng": 121.1}]
    m = _mission(MissionType.SCREEN, line=line)
    s0 = _run(m, _unit(24.05, 121.0), _wv(), MissionState())
    assert s0.orders[0].order_type == "MOVE"

    arrived = _run(m, _unit(24.0, 121.0), _wv(), s0.state)
    assert arrived.state.phase is MissionPhase.HOLDING

    enemy = {"unit_id": "r1", "lat": 24.0005, "lng": 121.0}
    holding = _run(m, _unit(24.0, 121.0), _wv([enemy]), arrived.state)
    assert holding.orders == [], "掩護幕不接戰"


# ---- MOVE_MARCH ----


def test_march_walks_every_waypoint_then_completes() -> None:
    route = [{"lat": 24.0, "lng": 121.0}, {"lat": 24.1, "lng": 121.0}]
    m = _mission(MissionType.MOVE_MARCH, route=route)
    s0 = _run(m, _unit(23.9, 121.0), _wv(), MissionState())
    assert s0.orders[0].payload["to_lat"] == pytest.approx(24.0)

    at1 = _run(m, _unit(24.0, 121.0), _wv(), s0.state)
    assert at1.state.waypoint_index == 1
    assert at1.orders[0].payload["to_lat"] == pytest.approx(24.1)

    at2 = _run(m, _unit(24.1, 121.0), _wv(), at1.state)
    assert at2.state.phase is MissionPhase.COMPLETE
    assert at2.orders == []


def test_terminal_phases_never_emit_more_orders() -> None:
    m = _mission(MissionType.MOVE_MARCH, route=[_OBJ])
    for phase in (MissionPhase.COMPLETE, MissionPhase.FAILED):
        out = _run(m, _unit(24.0, 121.0), _wv(), MissionState(phase))
        assert out.orders == [] and out.state.phase is phase


# ---- 失敗條件 ----


def test_destroyed_unit_fails_the_mission() -> None:
    m = _mission(MissionType.SEIZE, objective=_OBJ)
    out = _run(m, _unit(24.0, 121.0, status="DESTROYED"), _wv(), MissionState(MissionPhase.MOVING))
    assert out.state.phase is MissionPhase.FAILED


def test_unit_without_position_fails_the_mission() -> None:
    """位置不明（例如通聯中斷且從未回報）→ 規劃不了，明確失敗而不是靜靜卡住。"""
    m = _mission(MissionType.SEIZE, objective=_OBJ)
    out = step(m, MissionState(MissionPhase.MOVING), {"unit_id": "b1"}, _wv(), tick=3)
    assert out.state.phase is MissionPhase.FAILED
    assert out.note


# ---- 抵達容差 ----


def test_arrival_tolerance_is_honoured() -> None:
    """移動是逐 tick 推進，落點不會與目標完全重合——容差是必要的，不是偷懶。"""
    route = [{"lat": 24.0, "lng": 121.0}]
    m = _mission(MissionType.MOVE_MARCH, route=route)
    moving = MissionState(MissionPhase.MOVING)
    # 容差內（約 55 m）
    assert _run(m, _unit(24.0005, 121.0), _wv(), moving).state.phase is MissionPhase.COMPLETE
    # 容差外（約 555 m）
    assert _run(m, _unit(24.005, 121.0), _wv(), moving).state.phase is MissionPhase.MOVING
    assert ARRIVAL_TOLERANCE_M < 555.0
