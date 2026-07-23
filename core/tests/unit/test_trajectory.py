"""飛彈拋物線軌跡淨空判定（#飛彈）——障礙/地形阻隔、頂高、弧上高度。"""

from __future__ import annotations

from app.adjudication.trajectory import (
    ArcObstacle,
    apex_m,
    arc_height_at,
    obstacle_blocks_arc,
    terrain_blocks_arc,
)

_S = (121.20, 23.75)  # 發射（lng,lat）
_T = (121.30, 23.75)  # 目標，正東約 10km


def test_apex_and_arc_height() -> None:
    assert apex_m(10000, 0.25) == 2500.0
    # 頂點在中點最高、端點為 0。
    assert arc_height_at(0.5, 2500) == 2500.0
    assert arc_height_at(0.0, 2500) == 0.0
    assert arc_height_at(1.0, 2500) == 0.0
    assert 0 < arc_height_at(0.1, 2500) < 2500


def _sq(cx: float, cy: float, half: float) -> tuple[tuple[float, float], ...]:
    return (
        (cx - half, cy - half),
        (cx + half, cy - half),
        (cx + half, cy + half),
        (cx - half, cy + half),
    )


def test_flat_trajectory_obstacle_blocks_but_lofted_clears() -> None:
    # 中點一座 400m 高建築。低伸彈道（apex_ratio 0.03→頂高 300m）被擋；高拋（0.25→2500m）越過。
    obs = ArcObstacle("w", "BUILDING", "POLYGON", _sq(121.25, 23.75, 0.002), height_m=400.0)
    blocked = obstacle_blocks_arc(_S, _T, [obs], apex_ratio=0.03)
    assert blocked.blocked and blocked.reason == "OBSTACLE"
    assert not obstacle_blocks_arc(_S, _T, [obs], apex_ratio=0.25).blocked


def test_low_obstacle_midpath_does_not_block() -> None:
    # 障礙在中點（弧很高 ~2500m）+ 只有 5m → 不阻隔。
    obs = ArcObstacle("o", "OBSTACLE", "POLYGON", _sq(121.25, 23.75, 0.002), height_m=5.0)
    assert not obstacle_blocks_arc(_S, _T, [obs], apex_ratio=0.25).blocked


def test_obstacle_off_path_ignored() -> None:
    obs = ArcObstacle("far", "BUILDING", "POLYGON", _sq(121.25, 24.5, 0.002), height_m=999.0)
    assert not obstacle_blocks_arc(_S, _T, [obs]).blocked


def test_terrain_ridge_blocks_arc() -> None:
    # 中段一座高於弧頂的山脊 → 阻隔。ground_range 10km、apex 2500m；中點弧絕對高度≈2500m。
    samples = [(0.25, 100.0), (0.5, 4000.0), (0.75, 100.0)]  # 中點地形 4000m > 2500m
    r = terrain_blocks_arc(samples, 0.0, 0.0, 10000, apex_ratio=0.25)
    assert r.blocked and r.reason == "TERRAIN"


def test_terrain_below_arc_clears() -> None:
    samples = [(0.25, 300.0), (0.5, 500.0), (0.75, 300.0)]  # 皆遠低於弧
    assert not terrain_blocks_arc(samples, 0.0, 0.0, 10000, apex_ratio=0.25).blocked


def test_point_obstacle_radius_block() -> None:
    # 中點高 400m 點障礙（半徑 500m），低伸彈道被擋。
    obs = ArcObstacle("p", "OBSTACLE", "POINT", ((121.25, 23.75),), height_m=400.0, radius_m=500.0)
    assert obstacle_blocks_arc(_S, _T, [obs], apex_ratio=0.03).blocked
