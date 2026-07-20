"""A* 路徑規劃測試（O2.4）。

核心正確性用**手工建構的受控 HexGridCache**（真實 h3 鄰接 + 指定地形，最佳解可獨立以
Dijkstra 交叉驗證）；另有兩個整合測試：夾具 DTED 建出的真實 hex cache、以及真檔 realdata。

成本值斷言用 test-local MobilityMatrix（鏡像 contracts/mobility_matrix.json 的結構與量級），
另單獨測 `MobilityMatrix.default()` 讀取 shipped 契約可正常規劃。
"""

from __future__ import annotations

import heapq
import math
import time
from itertools import pairwise
from pathlib import Path

import h3
import pytest
from terrain.dted import DtedMap
from terrain.hexgrid import CellAttributes, HexGridBuilder, HexGridCache, TerrainClass
from terrain.mobility import MobilityMatrix
from terrain.pathfind import get_path

# 鏡像 contracts/mobility_matrix.json 的結構/量級，但值固定於測試內（不隨契約漂移）
_ALL_CLASSES = ["URBAN", "FOREST", "GRASSLAND", "WETLAND", "BARREN", "WATER", "MOUNTAIN"]
TEST_MATRIX = MobilityMatrix.from_dict(
    {
        "version": "test",
        "profiles": {
            "FOOT": {
                "URBAN": 1.2,
                "FOREST": 1.5,
                "GRASSLAND": 1.0,
                "WETLAND": 2.5,
                "BARREN": 1.1,
                "WATER": -1,
                "MOUNTAIN": 3.0,
            },
            "WHEELED": {
                "URBAN": 1.0,
                "FOREST": 4.0,
                "GRASSLAND": 1.0,
                "WETLAND": -1,
                "BARREN": 1.5,
                "WATER": -1,
                "MOUNTAIN": -1,
            },
            "BOAT": {
                "URBAN": -1,
                "FOREST": -1,
                "GRASSLAND": -1,
                "WETLAND": 2.0,
                "BARREN": -1,
                "WATER": 1.0,
                "MOUNTAIN": -1,
            },
            "AIR": dict.fromkeys(_ALL_CLASSES, 1.0),
        },
        "slope_penalty": {"FOOT": 1.0, "WHEELED": 3.0, "BOAT": 0.0, "AIR": 0.0},
    }
)

CENTER = (23.75, 121.25)
RES = 8


def _cell(h3_index: str, terrain_class: TerrainClass, slope_deg: float = 0.0) -> CellAttributes:
    lat, lng = h3.cell_to_latlng(h3_index)
    return CellAttributes(
        h3_index=h3_index,
        center_lat=lat,
        center_lng=lng,
        elevation_mean=0.0,
        elevation_max=0.0,
        slope_deg=slope_deg,
        terrain_class=terrain_class,
        water=terrain_class is TerrainClass.WATER,
        mobility_cost=1.0,
    )


def make_cache(
    classes: dict[str, TerrainClass], slopes: dict[str, float] | None = None
) -> HexGridCache:
    slopes = slopes or {}
    return HexGridCache({h: _cell(h, tc, slopes.get(h, 0.0)) for h, tc in classes.items()})


def uniform_patch(center: tuple[float, float], k: int, tc: TerrainClass) -> dict[str, TerrainClass]:
    origin = h3.latlng_to_cell(center[0], center[1], RES)
    return dict.fromkeys(h3.grid_disk(origin, k), tc)


def dijkstra_cost(
    cache: HexGridCache, matrix: MobilityMatrix, start: str, goal: str, profile: str
) -> float | None:
    """獨立最佳成本（均勻成本搜尋，無 heuristic）——交叉驗證 A* 的最佳性。"""
    if start == goal:
        return 0.0
    dist: dict[str, float] = {start: 0.0}
    pq: list[tuple[float, str]] = [(0.0, start)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == goal:
            return d
        if d > dist.get(u, math.inf):
            continue
        for v in set(h3.grid_disk(u, 1)) - {u}:
            cell = cache.get_cell(v)
            if cell is None:
                continue
            sc = matrix.step_cost(profile, str(cell.terrain_class), cell.slope_deg)
            if sc is None:
                continue
            nd = d + sc
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                heapq.heappush(pq, (nd, v))
    return None


# ---------------- 基本 / property ----------------


def test_trivial_same_cell() -> None:
    cache = make_cache(uniform_patch(CENTER, 1, TerrainClass.GRASSLAND))
    origin = h3.latlng_to_cell(*CENTER, RES)
    result = get_path(cache, origin, origin, "FOOT", TEST_MATRIX)
    assert result.reachable
    assert result.h3_path == [origin]
    assert result.total_cost == 0.0
    assert result.eta_ticks == 0


def test_adjacent_cells() -> None:
    cache = make_cache(uniform_patch(CENTER, 2, TerrainClass.GRASSLAND))
    origin = h3.latlng_to_cell(*CENTER, RES)
    neighbor = next(n for n in sorted(h3.grid_disk(origin, 1)) if n != origin)
    result = get_path(cache, origin, neighbor, "FOOT", TEST_MATRIX)
    assert result.reachable
    assert result.h3_path == [origin, neighbor]
    assert result.total_cost == pytest.approx(1.0)  # GRASSLAND FOOT=1.0, slope0


def test_path_endpoints_are_from_and_to() -> None:
    cache = make_cache(uniform_patch(CENTER, 3, TerrainClass.GRASSLAND))
    origin = h3.latlng_to_cell(*CENTER, RES)
    goal = sorted(h3.grid_disk(origin, 3))[-1]
    result = get_path(cache, origin, goal, "FOOT", TEST_MATRIX)
    assert result.reachable
    assert result.h3_path[0] == origin
    assert result.h3_path[-1] == goal


def test_consecutive_cells_are_adjacent() -> None:
    cache = make_cache(uniform_patch(CENTER, 3, TerrainClass.GRASSLAND))
    origin = h3.latlng_to_cell(*CENTER, RES)
    goal = sorted(h3.grid_disk(origin, 3))[-1]
    path = get_path(cache, origin, goal, "FOOT", TEST_MATRIX).h3_path
    for a, b in pairwise(path):
        assert b in set(h3.grid_disk(a, 1)) - {a}, f"{a}->{b} 非相鄰"


def test_optimal_matches_dijkstra_uniform() -> None:
    cache = make_cache(uniform_patch(CENTER, 3, TerrainClass.GRASSLAND))
    origin = h3.latlng_to_cell(*CENTER, RES)
    for goal in sorted(h3.grid_disk(origin, 3)):
        result = get_path(cache, origin, goal, "FOOT", TEST_MATRIX)
        expected = dijkstra_cost(cache, TEST_MATRIX, origin, goal, "FOOT")
        assert result.reachable is (expected is not None)
        if expected is not None:
            assert result.total_cost == pytest.approx(expected), f"goal {goal}"


def test_optimal_with_slope_costs() -> None:
    # 混合坡度 → 成本非均勻；A* 仍須等於獨立 Dijkstra 最佳解
    patch = uniform_patch(CENTER, 3, TerrainClass.GRASSLAND)
    slopes = {h: (i % 5) * 8.0 for i, h in enumerate(sorted(patch))}  # 0,8,16,24,32 度
    cache = make_cache(patch, slopes)
    origin = h3.latlng_to_cell(*CENTER, RES)
    for goal in sorted(patch)[::4]:
        result = get_path(cache, origin, goal, "FOOT", TEST_MATRIX)
        expected = dijkstra_cost(cache, TEST_MATRIX, origin, goal, "FOOT")
        if expected is not None:
            assert result.total_cost == pytest.approx(expected), f"goal {goal}"


def test_cost_le_any_adjacent_alternative() -> None:
    # property（TASKS 驗收）：最佳路徑成本 ≤ 任一「經某中間點的相鄰替代」路徑
    patch = uniform_patch(CENTER, 3, TerrainClass.GRASSLAND)
    slopes = {h: (i % 4) * 10.0 for i, h in enumerate(sorted(patch))}
    cache = make_cache(patch, slopes)
    origin = h3.latlng_to_cell(*CENTER, RES)
    goal = sorted(patch)[-1]
    best = get_path(cache, origin, goal, "FOOT", TEST_MATRIX)
    assert best.reachable
    # 對每個中間 cell 的每個可通行鄰居，繞經它的最佳成本不得更低
    for detour in sorted(patch):
        if detour in (origin, goal):
            continue
        c1 = dijkstra_cost(cache, TEST_MATRIX, origin, detour, "FOOT")
        c2 = dijkstra_cost(cache, TEST_MATRIX, detour, goal, "FOOT")
        if c1 is not None and c2 is not None:
            assert best.total_cost <= c1 + c2 + 1e-9


def test_deterministic_same_path() -> None:
    patch = uniform_patch(CENTER, 3, TerrainClass.GRASSLAND)
    slopes = {h: (i % 7) * 5.0 for i, h in enumerate(sorted(patch))}
    cache = make_cache(patch, slopes)
    origin = h3.latlng_to_cell(*CENTER, RES)
    goal = sorted(patch)[-1]
    r1 = get_path(cache, origin, goal, "FOOT", TEST_MATRIX)
    r2 = get_path(cache, origin, goal, "FOOT", TEST_MATRIX)
    assert r1.h3_path == r2.h3_path
    assert r1.total_cost == r2.total_cost


def test_eta_ticks_is_ceil_cost() -> None:
    patch = uniform_patch(CENTER, 3, TerrainClass.GRASSLAND)
    cache = make_cache(patch, dict.fromkeys(patch, 20.0))  # 非整數成本
    origin = h3.latlng_to_cell(*CENTER, RES)
    goal = sorted(patch)[-1]
    result = get_path(cache, origin, goal, "FOOT", TEST_MATRIX)
    assert result.eta_ticks == math.ceil(result.total_cost)


# ---------------- profile 差異化：繞行 / 不可通行 ----------------


def _mountain_blob_cache() -> tuple[HexGridCache, str]:
    """中央 blob（ring0+ring1）為 MOUNTAIN，其餘 GRASSLAND。回 (cache, origin)。"""
    origin = h3.latlng_to_cell(*CENTER, RES)
    classes = dict.fromkeys(h3.grid_disk(origin, 3), TerrainClass.GRASSLAND)
    for h in h3.grid_disk(origin, 1):  # 中央 7 格設為山
        classes[h] = TerrainClass.MOUNTAIN
    return make_cache(classes), origin


def test_wheeled_routes_around_mountain() -> None:
    cache, origin = _mountain_blob_cache()
    # 起訖取 ring3 對向兩點（都在 GRASSLAND）
    ring3 = sorted(h3.grid_ring(origin, 3))
    start, goal = ring3[0], ring3[len(ring3) // 2]
    result = get_path(cache, start, goal, "WHEELED", TEST_MATRIX)
    assert result.reachable
    # WHEELED 不可走 MOUNTAIN → 路徑不得含任何山格
    for h in result.h3_path:
        assert cache.get_cell(h).terrain_class is not TerrainClass.MOUNTAIN


def test_foot_may_cross_mountain_cheaper_than_wheeled_detour() -> None:
    cache, origin = _mountain_blob_cache()
    ring3 = sorted(h3.grid_ring(origin, 3))
    start, goal = ring3[0], ring3[len(ring3) // 2]
    foot = get_path(cache, start, goal, "FOOT", TEST_MATRIX)
    wheeled = get_path(cache, start, goal, "WHEELED", TEST_MATRIX)
    assert foot.reachable and wheeled.reachable
    # FOOT 可穿山（雖每格 3.0），WHEELED 須繞路；兩者都最佳，成本各自等於其 Dijkstra
    assert foot.total_cost == pytest.approx(dijkstra_cost(cache, TEST_MATRIX, start, goal, "FOOT"))
    assert wheeled.total_cost == pytest.approx(
        dijkstra_cost(cache, TEST_MATRIX, start, goal, "WHEELED")
    )


def test_walled_interior_unreachable() -> None:
    # ring2 整圈設為 MOUNTAIN → WHEELED 從中心無法到 ring3
    origin = h3.latlng_to_cell(*CENTER, RES)
    classes = dict.fromkeys(h3.grid_disk(origin, 3), TerrainClass.GRASSLAND)
    for h in h3.grid_ring(origin, 2):
        classes[h] = TerrainClass.MOUNTAIN
    cache = make_cache(classes)
    goal = sorted(h3.grid_ring(origin, 3))[0]
    result = get_path(cache, origin, goal, "WHEELED", TEST_MATRIX)
    assert not result.reachable
    assert result.h3_path == []
    # 同一牆對 FOOT（可穿山）則可達
    assert get_path(cache, origin, goal, "FOOT", TEST_MATRIX).reachable


def test_boat_on_land_unreachable() -> None:
    cache = make_cache(uniform_patch(CENTER, 2, TerrainClass.GRASSLAND))
    origin = h3.latlng_to_cell(*CENTER, RES)
    goal = sorted(h3.grid_disk(origin, 2))[-1]
    assert not get_path(cache, origin, goal, "BOAT", TEST_MATRIX).reachable  # 陸地起點不可通行


def test_wheeled_into_water_goal_unreachable() -> None:
    patch = uniform_patch(CENTER, 3, TerrainClass.GRASSLAND)
    origin = h3.latlng_to_cell(*CENTER, RES)
    goal = sorted(h3.grid_disk(origin, 3))[-1]
    patch[goal] = TerrainClass.WATER  # 目標為水
    cache = make_cache(patch)
    assert not get_path(cache, origin, goal, "WHEELED", TEST_MATRIX).reachable


def test_boat_water_to_water_reachable() -> None:
    cache = make_cache(uniform_patch(CENTER, 2, TerrainClass.WATER))
    origin = h3.latlng_to_cell(*CENTER, RES)
    goal = sorted(h3.grid_disk(origin, 2))[-1]
    result = get_path(cache, origin, goal, "BOAT", TEST_MATRIX)
    assert result.reachable
    assert result.total_cost == pytest.approx(
        dijkstra_cost(cache, TEST_MATRIX, origin, goal, "BOAT")
    )


def test_air_ignores_terrain() -> None:
    # AIR 全地形成本 1.0；山也照飛 → 直線最短跳
    cache, origin = _mountain_blob_cache()
    ring3 = sorted(h3.grid_ring(origin, 3))
    start, goal = ring3[0], ring3[len(ring3) // 2]
    result = get_path(cache, start, goal, "AIR", TEST_MATRIX)
    assert result.reachable
    assert result.total_cost == pytest.approx(dijkstra_cost(cache, TEST_MATRIX, start, goal, "AIR"))


# ---------------- 錯誤與邊界 ----------------


def test_unknown_profile_raises() -> None:
    cache = make_cache(uniform_patch(CENTER, 1, TerrainClass.GRASSLAND))
    origin = h3.latlng_to_cell(*CENTER, RES)
    with pytest.raises(ValueError, match="mobility_profile"):
        get_path(cache, origin, origin, "SUBMARINE", TEST_MATRIX)


def test_resolution_mismatch_raises() -> None:
    cache = make_cache(uniform_patch(CENTER, 1, TerrainClass.GRASSLAND))
    a = h3.latlng_to_cell(*CENTER, 8)
    b = h3.latlng_to_cell(*CENTER, 7)
    with pytest.raises(ValueError, match="解析度"):
        get_path(cache, a, b, "FOOT", TEST_MATRIX)


def test_endpoint_not_in_cache_unreachable() -> None:
    cache = make_cache(uniform_patch(CENTER, 1, TerrainClass.GRASSLAND))
    origin = h3.latlng_to_cell(*CENTER, RES)
    far = h3.latlng_to_cell(25.0, 121.5, RES)  # 遠在快取外
    assert not get_path(cache, origin, far, "FOOT", TEST_MATRIX).reachable
    assert not get_path(cache, far, origin, "FOOT", TEST_MATRIX).reachable


# ---------------- 效能 ----------------


def test_get_path_p99_under_100ms() -> None:
    cache = make_cache(uniform_patch(CENTER, 6, TerrainClass.GRASSLAND))  # 較大區域
    origin = h3.latlng_to_cell(*CENTER, RES)
    goal = sorted(h3.grid_disk(origin, 6))[-1]
    latencies: list[float] = []
    for _ in range(100):
        t0 = time.perf_counter()
        get_path(cache, origin, goal, "FOOT", TEST_MATRIX)
        latencies.append(time.perf_counter() - t0)
    latencies.sort()
    p99 = latencies[int(100 * 0.99) - 1]
    assert p99 < 0.100, f"get_path p99 {p99 * 1000:.2f}ms ≥ 100ms（SPEC §4.3）"


# ---------------- shipped 契約整合 ----------------


def test_default_matrix_loads_and_paths() -> None:
    matrix = MobilityMatrix.default()  # 讀 contracts/mobility_matrix.json
    assert matrix.has_profile("FOOT")
    cache = make_cache(uniform_patch(CENTER, 2, TerrainClass.GRASSLAND))
    origin = h3.latlng_to_cell(*CENTER, RES)
    goal = sorted(h3.grid_disk(origin, 2))[-1]
    result = get_path(cache, origin, goal, "FOOT", matrix)
    assert result.reachable and result.total_cost > 0


# ---------------- 夾具 DTED 整合（HexGridBuilder → get_path） ----------------


@pytest.fixture(scope="module")
def fixture_cache(fixture_tiff: Path) -> HexGridCache:
    with DtedMap.open(fixture_tiff) as dted:
        builder = HexGridBuilder(dted)
        cells = {c.h3_index: c for c in builder.build_region((121.10, 23.60, 121.50, 23.90), 7)}
    return HexGridCache(cells)


def test_fixture_foot_path_across_mountain(fixture_cache: HexGridCache) -> None:
    # 真實由 GeoTIFF 建出的 hex cache（多為 MOUNTAIN，FOOT 可穿）
    west = h3.latlng_to_cell(23.75, 121.13, 7)
    east = h3.latlng_to_cell(23.75, 121.37, 7)
    assert fixture_cache.get_cell(west) is not None
    assert fixture_cache.get_cell(east) is not None
    result = get_path(fixture_cache, west, east, "FOOT", TEST_MATRIX)
    assert result.reachable
    assert result.h3_path[0] == west and result.h3_path[-1] == east
    assert result.total_cost == pytest.approx(
        dijkstra_cost(fixture_cache, TEST_MATRIX, west, east, "FOOT")
    )


# ---------------- 真檔 realdata ----------------


@pytest.mark.realdata
def test_real_small_region_path_p99(real_dted_path: Path) -> None:
    # 由真檔建小區域 hex cache（玉山山腳一帶）→ FOOT 規劃可達且 p99<100ms
    with DtedMap.open(real_dted_path) as dted:
        builder = HexGridBuilder(dted)
        cells = {c.h3_index: c for c in builder.build_region((120.90, 23.40, 120.98, 23.48), 8)}
    cache = HexGridCache(cells)
    start = min(cells)
    goal = max(cells)
    latencies: list[float] = []
    for _ in range(50):
        t0 = time.perf_counter()
        result = get_path(cache, start, goal, "FOOT", TEST_MATRIX)
        latencies.append(time.perf_counter() - t0)
    assert max(latencies) < 0.100, f"真檔 get_path max {max(latencies) * 1000:.1f}ms ≥ 100ms"
    # 可達性依地形而定，但至少不得拋例外、且回傳結構正確
    assert isinstance(result.reachable, bool)
