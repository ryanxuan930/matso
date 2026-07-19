"""Hex grid 預計算 / 快取測試（合成夾具）。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from make_fixture import BASE_M, PEAK_LAT, PEAK_LNG
from terrain.dted import DtedMap
from terrain.hexgrid import (
    HexGridBuilder,
    HexGridCache,
    TerrainClass,
    base_mobility_cost,
    classify_terrain,
    write_parquet,
)
from terrain.precompute import main as precompute_main

RES = 8
FIXTURE_BBOX = (121.0, 23.5, 121.5, 24.0)


@pytest.fixture(scope="module")
def builder(fixture_tiff: Path):  # type: ignore[no-untyped-def]
    with DtedMap.open(fixture_tiff) as d:
        yield HexGridBuilder(d)


# ---------------- classify_terrain 規則 ----------------


def test_classify_water() -> None:
    assert classify_terrain(0.0, 0.0, water=True) is TerrainClass.WATER


def test_classify_mountain_by_elevation() -> None:
    assert classify_terrain(1500.0, 5.0, water=False) is TerrainClass.MOUNTAIN


def test_classify_mountain_by_slope() -> None:
    assert classify_terrain(300.0, 30.0, water=False) is TerrainClass.MOUNTAIN


def test_classify_barren_steep() -> None:
    assert classify_terrain(300.0, 15.0, water=False) is TerrainClass.BARREN


def test_classify_wetland_low_flat() -> None:
    assert classify_terrain(2.0, 1.0, water=False) is TerrainClass.WETLAND


def test_classify_grassland_default() -> None:
    assert classify_terrain(300.0, 3.0, water=False) is TerrainClass.GRASSLAND


def test_mobility_cost_increases_with_slope() -> None:
    flat = base_mobility_cost(TerrainClass.GRASSLAND, 0.0)
    steep = base_mobility_cost(TerrainClass.BARREN, 20.0)
    assert steep > flat
    assert base_mobility_cost(TerrainClass.WATER, 0.0) > 100  # 水域高懲罰


# ---------------- build_cell 屬性正確性（對照夾具解析式） ----------------


def test_peak_cell_attributes(builder: HexGridBuilder) -> None:
    import h3

    cell = h3.latlng_to_cell(PEAK_LAT, PEAK_LNG, RES)
    attrs = builder.build_cell(cell)
    assert not attrs.water
    # 峰頂 cell 高程 mean 應接近 BASE+PEAK 級別（cell 涵蓋一小片山頂，略低於峰值）
    assert attrs.elevation_max == pytest.approx(BASE_M + 3000.0, abs=200.0)
    assert attrs.elevation_mean > 2000.0
    assert attrs.terrain_class is TerrainClass.MOUNTAIN
    assert attrs.center_lat == pytest.approx(PEAK_LAT, abs=0.02)


def test_sea_cell_is_water(builder: HexGridBuilder) -> None:
    import h3

    cell = h3.latlng_to_cell(23.75, 121.47, RES)  # 東側海域
    attrs = builder.build_cell(cell)
    assert attrs.water
    assert attrs.terrain_class is TerrainClass.WATER
    assert attrs.elevation_mean == 0.0


def test_below_sea_cell_is_land_not_water(builder: HexGridBuilder) -> None:
    import h3

    cell = h3.latlng_to_cell(23.7, 121.02, RES)  # 低於海平面陸地帶
    attrs = builder.build_cell(cell)
    assert not attrs.water  # 負高程不得被當水域
    assert attrs.elevation_mean < 0.0


def test_slope_higher_on_mountain_than_flat(builder: HexGridBuilder) -> None:
    import h3

    peak_cell = builder.build_cell(h3.latlng_to_cell(23.72, 121.18, RES))  # 山坡
    sea_cell = builder.build_cell(h3.latlng_to_cell(23.75, 121.47, RES))  # 海（坡度 0）
    assert peak_cell.slope_deg > sea_cell.slope_deg
    assert sea_cell.slope_deg == 0.0


# ---------------- parquet 快取 roundtrip / 查詢 ----------------


def test_parquet_roundtrip(builder: HexGridBuilder, tmp_path: Path) -> None:
    import h3

    cells = [builder.build_cell(c) for c in list(h3.polygon_to_cells(_poly(), RES))[:100]]
    out = tmp_path / "res8.parquet"
    n = write_parquet(cells, out)
    assert n == len(cells)

    cache = HexGridCache.open(out)
    assert len(cache) == len(cells)
    for original in cells:
        loaded = cache.get_cell(original.h3_index)
        assert loaded is not None
        assert loaded.h3_index == original.h3_index
        assert loaded.terrain_class == original.terrain_class
        assert loaded.elevation_mean == pytest.approx(original.elevation_mean, abs=0.01)
        assert loaded.water == original.water


def test_get_cell_batch_partial(builder: HexGridBuilder, tmp_path: Path) -> None:
    import h3

    real = [builder.build_cell(c) for c in list(h3.polygon_to_cells(_poly(), RES))[:20]]
    out = tmp_path / "res8.parquet"
    write_parquet(real, out)
    cache = HexGridCache.open(out)

    real_ids = [c.h3_index for c in real]
    missing = "8000000000000000"  # 不存在的 cell
    result = cache.get_cell_batch([*real_ids[:5], missing])
    assert set(result) == set(real_ids[:5])  # 缺的不出現
    assert missing not in result


def test_cache_query_p99_under_20ms(builder: HexGridBuilder, tmp_path: Path) -> None:
    import h3

    cells = [builder.build_cell(c) for c in list(h3.polygon_to_cells(_poly(), RES))[:500]]
    out = tmp_path / "res8.parquet"
    write_parquet(cells, out)
    cache = HexGridCache.open(out)  # 載入後純記憶體查詢（不需 DTED / 外接硬碟）

    ids = [c.h3_index for c in cells]
    latencies: list[float] = []
    for _ in range(1000):
        t0 = time.perf_counter()
        cache.get_cell_batch(ids[:50])
        latencies.append(time.perf_counter() - t0)
    latencies.sort()
    p99 = latencies[int(1000 * 0.99) - 1]
    assert p99 < 0.020, f"p99 {p99 * 1000:.2f}ms ≥ 20ms（SPEC §4.3）"


# ---------------- build_region / CLI ----------------


def test_build_region_covers_bbox(builder: HexGridBuilder) -> None:
    import h3

    cells = list(builder.build_region(FIXTURE_BBOX, RES))
    expected_count = len(h3.polygon_to_cells(_poly(), RES))
    assert len(cells) == expected_count
    assert all(c.terrain_class in set(TerrainClass) for c in cells)


def test_build_region_rejects_bad_resolution(builder: HexGridBuilder) -> None:
    with pytest.raises(ValueError, match="resolution"):
        next(builder.build_region(FIXTURE_BBOX, 6))


def test_precompute_cli_writes_cache(fixture_tiff: Path, tmp_path: Path) -> None:
    out = tmp_path / "cli_res8.parquet"
    rc = precompute_main(
        [
            "--dted",
            str(fixture_tiff),
            "--bbox",
            "121.2",
            "23.7",
            "121.3",
            "23.8",
            "--res",
            "8",
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    assert out.is_file()
    cache = HexGridCache.open(out)
    assert len(cache) > 0


def _poly():  # type: ignore[no-untyped-def]
    import h3

    min_lng, min_lat, max_lng, max_lat = FIXTURE_BBOX
    return h3.LatLngPoly(
        [(min_lat, min_lng), (min_lat, max_lng), (max_lat, max_lng), (max_lat, min_lng)]
    )
