"""移動路徑成本 / 耗損模型（#28）——純函數：幾何穿越、成本估計、強穿隨機耗損。"""

from __future__ import annotations

from app.engine.rng import DeterministicRNG
from app.movement.attrition import (
    Crossing,
    Obstacle,
    classify_crossings,
    estimate_route,
    forced_extra_attrition,
    is_impassable,
    obstacle_from_feature,
    point_in_ring,
    route_distance_m,
    segments_intersect,
)

# 台灣東部一小塊區域（lng, lat）。
_A = (121.20, 23.75)
_B = (121.30, 23.75)  # 正東約 10km


def _square(cx: float, cy: float, half: float) -> list[list[float]]:
    return [
        [cx - half, cy - half],
        [cx + half, cy - half],
        [cx + half, cy + half],
        [cx - half, cy + half],
    ]


def test_is_impassable_kinds() -> None:
    assert is_impassable("OBSTACLE", None)
    assert is_impassable("BUILDING", {})
    assert not is_impassable("CONTROL_MEASURE", None)
    assert not is_impassable("TERRAIN", None)
    assert is_impassable("TERRAIN", {"impassable": True})


def test_route_distance_east_leg() -> None:
    d = route_distance_m([_A, _B])
    assert 9_000 < d < 11_500  # ~10km


def test_segments_intersect_cross_and_miss() -> None:
    assert segments_intersect((0, 0), (2, 2), (0, 2), (2, 0))
    assert not segments_intersect((0, 0), (1, 0), (0, 1), (1, 1))


def test_point_in_ring() -> None:
    ring = [(0, 0), (2, 0), (2, 2), (0, 2)]
    assert point_in_ring((1, 1), ring)
    assert not point_in_ring((3, 3), ring)


def test_obstacle_from_feature_filters_non_impassable() -> None:
    assert obstacle_from_feature({"kind": "CONTROL_MEASURE", "geometry_type": "LINE"}) is None
    feat = {
        "id": "f1",
        "kind": "OBSTACLE",
        "geometry_type": "POLYGON",
        "geometry": _square(121.25, 23.75, 0.01),
        "label": "雷區",
    }
    obs = obstacle_from_feature(feat)
    assert obs is not None and obs.kind == "OBSTACLE" and obs.label == "雷區"


def test_classify_crossings_polygon_on_path() -> None:
    # 障礙方塊擋在 A→B 中點附近 → 應偵測到穿越。
    obs = obstacle_from_feature(
        {
            "id": "blk",
            "kind": "OBSTACLE",
            "geometry_type": "POLYGON",
            "geometry": _square(121.25, 23.75, 0.01),
        }
    )
    assert obs is not None
    crossings = classify_crossings([_A, _B], [obs])
    assert len(crossings) == 1 and crossings[0].feature_id == "blk"
    assert 0.0 <= crossings[0].entry_frac <= 1.0


def test_classify_crossings_off_path_none() -> None:
    obs = obstacle_from_feature(
        {
            "id": "far",
            "kind": "BUILDING",
            "geometry_type": "POLYGON",
            "geometry": _square(121.25, 24.50, 0.01),  # 遠在北方
        }
    )
    assert obs is not None
    assert classify_crossings([_A, _B], [obs]) == []


def test_point_obstacle_radius() -> None:
    obs = Obstacle("p", "OBSTACLE", "POINT", ((121.25, 23.75),), radius_m=2000.0)
    assert len(classify_crossings([_A, _B], [obs])) == 1
    far = Obstacle("p", "OBSTACLE", "POINT", ((121.25, 23.90),), radius_m=500.0)
    assert classify_crossings([_A, _B], [far]) == []


def test_estimate_route_feasible_vs_forced() -> None:
    clear = estimate_route([_A, _B], [], speed_kmh=40.0, tick_rate_ms=1000.0)
    assert clear.feasible and not clear.forced and clear.duration_ticks > 0
    assert clear.fuel_cost > 0
    obs = obstacle_from_feature(
        {
            "id": "b",
            "kind": "BUILDING",
            "geometry_type": "POLYGON",
            "geometry": _square(121.25, 23.75, 0.01),
        }
    )
    forced = estimate_route([_A, _B], [obs], speed_kmh=40.0, tick_rate_ms=1000.0)
    assert forced.forced and not forced.feasible and len(forced.crossings) == 1


def test_forced_extra_attrition_deterministic_and_capped() -> None:
    crossings = [
        Crossing("a", "BUILDING", None, 0.2),
        Crossing("b", "OBSTACLE", None, 0.6),
    ]
    rng1 = DeterministicRNG(42, "movement")
    rng2 = DeterministicRNG(42, "movement")
    loss1 = forced_extra_attrition(crossings, 100.0, rng1)
    loss2 = forced_extra_attrition(crossings, 100.0, rng2)
    assert loss1 == loss2  # 同 seed → 同結果（replay 安全）
    assert 0.0 < loss1 <= 40.0  # 封頂 40%


def test_forced_extra_attrition_zero_when_no_crossing() -> None:
    rng = DeterministicRNG(1, "movement")
    assert forced_extra_attrition([], 100.0, rng) == 0.0
    assert forced_extra_attrition([Crossing("a", "OBSTACLE", None, 0.1)], 0.0, rng) == 0.0
