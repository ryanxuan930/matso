"""LOS / Viewshed 測試（合成夾具）：property + 已知幾何 + p99 benchmark。

夾具幾何（make_fixture）：BASE_M=50 的餘弦山峰，峰頂 (23.75,121.25) ≈ 3050m，FALLOFF 0.5°；
東側 lng>121.4 為海（0）。可據此建構「山遮蔽」情境。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from terrain.dted import DtedMap
from terrain.los import Observer, check_los, get_viewshed

# 山兩側對稱點（峰在中間）：AGL 0 時被山遮蔽，抬高 AGL 後互見
WEST = Observer(lat=23.75, lng=121.12)  # 西坡 ~2100m
EAST = Observer(lat=23.75, lng=121.38)  # 東坡 ~2100m
PEAK_LNG = 121.25


@pytest.fixture(scope="module")
def dted(fixture_tiff: Path):  # type: ignore[no-untyped-def]
    with DtedMap.open(fixture_tiff) as d:
        yield d


def _agl(o: Observer, h: float) -> Observer:
    return Observer(lat=o.lat, lng=o.lng, height_agl_m=h)


# ---------------- property / 基本 ----------------


def test_self_sees_self(dted: DtedMap) -> None:
    assert check_los(dted, WEST, WEST).visible


def test_adjacent_points_visible(dted: DtedMap) -> None:
    # 距離 ≤ 一個步長 → 中間無取樣點 → 互見（短路徑）
    a = Observer(lat=23.75, lng=121.250)
    b = Observer(lat=23.7501, lng=121.250)  # ~11m
    assert check_los(dted, a, b).visible


def test_flat_sea_visible(dted: DtedMap) -> None:
    # 海面（nodata→0）上兩點、低 AGL → 平坦互見
    a = Observer(lat=23.75, lng=121.44, height_agl_m=10)
    b = Observer(lat=23.70, lng=121.46, height_agl_m=10)
    assert check_los(dted, a, b).visible


def test_mountain_blocks_at_ground_level(dted: DtedMap) -> None:
    result = check_los(dted, WEST, EAST)
    assert not result.visible
    assert result.clearance_m < 0
    assert result.obstruction_lng is not None
    assert PEAK_LNG - 0.05 < result.obstruction_lng < PEAK_LNG + 0.05  # 遮蔽點在峰附近


def test_raising_agl_restores_visibility(dted: DtedMap) -> None:
    assert not check_los(dted, WEST, EAST).visible
    high = check_los(dted, _agl(WEST, 1500), _agl(EAST, 1500))
    assert high.visible


def test_occlusion_monotonic_in_agl(dted: DtedMap) -> None:
    # 抬高 AGL 不得使原本可見變不可見（單調性）
    seen = False
    for h in (0, 200, 400, 600, 800, 1000, 1200, 1500, 2000):
        visible = check_los(dted, _agl(WEST, h), _agl(EAST, h)).visible
        if seen:
            assert visible, f"AGL {h}: 曾可見卻又不可見（違反單調性）"
        seen = seen or visible
    assert seen  # 夠高時最終可見


def test_clearance_positive_when_visible(dted: DtedMap) -> None:
    result = check_los(dted, _agl(WEST, 2000), _agl(EAST, 2000))
    assert result.visible
    assert result.clearance_m > 0
    assert result.obstruction_lat is None


def test_curvature_reduces_clearance_over_distance(dted: DtedMap) -> None:
    # 海面上等高兩點：clearance 應略小於 AGL（地球曲率下沉），且仍可見
    a = Observer(lat=23.75, lng=121.42, height_agl_m=50)
    b = Observer(lat=23.60, lng=121.42, height_agl_m=50)
    result = check_los(dted, a, b)
    assert result.visible
    assert result.clearance_m < 50.0  # 曲率使餘隙 < 天線高


# ---------------- viewshed ----------------


def test_viewshed_basic(dted: DtedMap) -> None:
    observer = Observer(lat=23.75, lng=121.25, height_agl_m=5)  # 峰頂
    cells = get_viewshed(dted, observer, radius_m=1500)
    assert len(cells) > 0
    import h3

    origin = h3.latlng_to_cell(observer.lat, observer.lng, 8)
    assert origin in cells  # 觀測者自身 cell 可見


def test_viewshed_all_within_radius_and_bounds(dted: DtedMap) -> None:
    import h3
    from terrain.los import _haversine_m

    observer = Observer(lat=23.75, lng=121.25, height_agl_m=5)
    radius = 1200.0
    for cell in get_viewshed(dted, observer, radius_m=radius):
        lat, lng = h3.cell_to_latlng(cell)
        assert _haversine_m(observer.lat, observer.lng, lat, lng) <= radius
        assert dted.contains(lat, lng)


def test_viewshed_higher_agl_sees_more(dted: DtedMap) -> None:
    low = get_viewshed(dted, Observer(lat=23.72, lng=121.18, height_agl_m=2), radius_m=2000)
    high = get_viewshed(dted, Observer(lat=23.72, lng=121.18, height_agl_m=500), radius_m=2000)
    assert len(high) >= len(low)


def test_viewshed_rejects_nonpositive_radius(dted: DtedMap) -> None:
    with pytest.raises(ValueError, match="radius_m"):
        get_viewshed(dted, Observer(lat=23.75, lng=121.25), radius_m=0)


# ---------------- 效能 ----------------


@pytest.mark.benchmark
def test_check_los_p99_under_20ms(dted: DtedMap) -> None:
    latencies: list[float] = []
    for _ in range(200):
        t0 = time.perf_counter()
        check_los(dted, WEST, EAST)  # ~26km、~880 取樣點，但整條線一次讀入記憶體
        latencies.append(time.perf_counter() - t0)
    latencies.sort()
    p99 = latencies[int(200 * 0.99) - 1]
    assert p99 < 0.020, f"check_los p99 {p99 * 1000:.2f}ms ≥ 20ms（SPEC §4.3）"


@pytest.mark.benchmark
def test_viewshed_p99_under_200ms(dted: DtedMap) -> None:
    observer = Observer(lat=23.75, lng=121.25, height_agl_m=5)
    latencies: list[float] = []
    for _ in range(20):
        t0 = time.perf_counter()
        get_viewshed(dted, observer, radius_m=1500)
        latencies.append(time.perf_counter() - t0)
    latencies.sort()
    p99 = latencies[int(20 * 0.99) - 1] if len(latencies) >= 100 else max(latencies)
    assert p99 < 0.200, f"viewshed p99 {p99 * 1000:.2f}ms ≥ 200ms（SPEC §4.3）"


# ---------------- 真檔（realdata：外接硬碟 + MATSO_DTED_PATH 才跑） ----------------


@pytest.mark.realdata
def test_real_yushan_blocks_los(real_dted_path: Path) -> None:
    # 玉山（~3952m, 23.47,120.96）擋住兩側低處的視線
    with DtedMap.open(real_dted_path) as dted:
        west = Observer(lat=23.47, lng=120.85)
        east = Observer(lat=23.47, lng=121.08)
        assert not check_los(dted, west, east).visible
        # 抬到玉山之上 → 互見
        assert check_los(dted, _agl(west, 4000), _agl(east, 4000)).visible


@pytest.mark.realdata
def test_real_check_los_p99_under_20ms(real_dted_path: Path) -> None:
    with DtedMap.open(real_dted_path) as dted:
        west = Observer(lat=23.47, lng=120.85)
        east = Observer(lat=23.47, lng=121.08)
        latencies: list[float] = []
        for _ in range(200):
            t0 = time.perf_counter()
            check_los(dted, west, east)
            latencies.append(time.perf_counter() - t0)
        latencies.sort()
        p99 = latencies[int(200 * 0.99) - 1]
        assert p99 < 0.020, f"真檔 check_los p99 {p99 * 1000:.2f}ms ≥ 20ms"


@pytest.mark.realdata
def test_real_viewshed_p99_under_200ms(real_dted_path: Path) -> None:
    with DtedMap.open(real_dted_path) as dted:
        observer = Observer(lat=23.47, lng=120.96, height_agl_m=10)  # 玉山頂
        latencies: list[float] = []
        for _ in range(20):
            t0 = time.perf_counter()
            get_viewshed(dted, observer, radius_m=3000)
            latencies.append(time.perf_counter() - t0)
        assert max(latencies) < 0.200, f"真檔 viewshed max {max(latencies) * 1000:.1f}ms ≥ 200ms"
