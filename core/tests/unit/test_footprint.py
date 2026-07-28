"""武器/雷達射界地形裁切（footprint viewshed fan）——純幾何 + 注入式 LOS（#11）。"""

from __future__ import annotations

import math

import pytest

from app.footprint import bearings, compute_footprint, dest_point


def _always_clear(_obs, _tgt):
    return True, math.inf


def test_dest_point_north_moves_lat_up() -> None:
    lat, lng = dest_point(25.0, 121.0, 0.0, 1000.0)
    assert lat > 25.0  # 正北 → 緯度增加
    assert lng == pytest.approx(121.0, abs=1e-6)  # 經度幾乎不變


def test_dest_point_east_moves_lng_up() -> None:
    lat, lng = dest_point(25.0, 121.0, 90.0, 1000.0)
    assert lng > 121.0  # 正東 → 經度增加
    assert lat == pytest.approx(25.0, abs=1e-3)


def test_bearings_full_circle_no_duplicate_wrap() -> None:
    bs = bearings(None, 360.0, 12)
    assert len(bs) == 12  # 全圓 n 個等距方位，不重複 0/360
    assert bs[0] == pytest.approx(0.0)
    assert max(bs) < 360.0


def test_bearings_sector_includes_endpoints() -> None:
    bs = bearings(90.0, 60.0, 6)
    assert len(bs) == 7  # steps+1（含兩端）
    assert bs[0] == pytest.approx(60.0)  # 90 - 30
    assert bs[-1] == pytest.approx(120.0)  # 90 + 30


def test_full_circle_unobstructed_reaches_max_range() -> None:
    fp = compute_footprint(
        lng=121.0,
        lat=25.0,
        max_range_m=2000.0,
        direction_deg=None,
        arc_deg=360.0,
        steps=24,
        observer_height_m=10.0,
        target_height_m=2.0,
        los_range=_always_clear,
    )
    assert not fp.clipped
    assert all(s.range_m == pytest.approx(2000.0) for s in fp.samples)
    # 全圓環閉合（首＝尾）。
    assert fp.ring[0] == fp.ring[-1]


def test_blocked_bearing_clips_range() -> None:
    # 東向（bearing≈90）遮蔽於 500m；其餘全通。
    def los(obs, tgt):
        _olat, _olng, _oh = obs
        _tlat, tlng, _th = tgt
        # 目標經度明顯大於射源 → 判為朝東 → 遮蔽。
        if tlng - 121.0 > 0.005:
            return False, 500.0
        return True, math.inf

    fp = compute_footprint(
        lng=121.0,
        lat=25.0,
        max_range_m=3000.0,
        direction_deg=90.0,
        arc_deg=30.0,
        steps=8,
        observer_height_m=10.0,
        target_height_m=2.0,
        los_range=los,
    )
    assert fp.clipped
    assert any(s.range_m <= 500.0 for s in fp.samples)
    # 扇形頂點＝射源；環首與尾都應為射源座標。
    assert fp.ring[0] == pytest.approx([121.0, 25.0])
    assert fp.ring[-1] == pytest.approx([121.0, 25.0])


def test_zero_range_returns_empty() -> None:
    fp = compute_footprint(
        lng=121.0,
        lat=25.0,
        max_range_m=0.0,
        direction_deg=0.0,
        arc_deg=90.0,
        steps=8,
        observer_height_m=10.0,
        target_height_m=2.0,
        los_range=_always_clear,
    )
    assert fp.ring == []
    assert fp.samples == []
    assert not fp.clipped


def test_clear_range_clamped_to_max() -> None:
    # LOS 回報通視距離超過 max_range（不可見但遮蔽點更遠）→ 夾到 max。
    def los(_obs, _tgt):
        return False, 9999.0

    fp = compute_footprint(
        lng=121.0,
        lat=25.0,
        max_range_m=1500.0,
        direction_deg=0.0,
        arc_deg=45.0,
        steps=4,
        observer_height_m=10.0,
        target_height_m=2.0,
        los_range=los,
    )
    assert all(s.range_m <= 1500.0 for s in fp.samples)
