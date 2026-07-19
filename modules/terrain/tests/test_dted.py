"""DtedMap 單元測試（合成夾具）+ 真檔 realdata benchmark（外接硬碟在場才跑）。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from make_fixture import NODATA, PEAK_LAT, PEAK_LNG, PEAK_M, expected_elevation, write_fixture
from terrain.config import TerrainSettings
from terrain.dted import DtedMap, ElevationResult
from terrain.errors import DtedFileNotFoundError, OutOfBoundsError

# 取樣容差：查詢點回傳的是「所在像素中心」的值。夾具最大坡度 ≈ PEAK_M·π/FALLOFF ≈
# 18,850 m/deg，像素 0.0025°、中心最大偏移為半對角 ≈ 0.00177° → 誤差上界 ≈ 33m。
TOL_M = 40.0


@pytest.fixture(scope="module")
def dted(fixture_tiff: Path):  # type: ignore[no-untyped-def]
    with DtedMap.open(fixture_tiff) as d:
        yield d


# ---------------- 已知點高程 ----------------


def test_peak_elevation(dted: DtedMap) -> None:
    result = dted.get_elevation(PEAK_LAT, PEAK_LNG)
    assert not result.water
    assert result.elevation_m == pytest.approx(PEAK_M, abs=TOL_M)


@pytest.mark.parametrize(
    ("lat", "lng"),
    [
        (23.75, 121.1),  # 山腰（西側）
        (23.6, 121.25),  # 山腰（南側）
        (23.9, 121.35),  # 山腳
        (23.51, 121.01),  # 角落（遠處，趨近 0）
    ],
)
def test_known_points_match_closed_form(dted: DtedMap, lat: float, lng: float) -> None:
    expected = expected_elevation(lat, lng)
    assert expected is not None
    result = dted.get_elevation(lat, lng)
    assert not result.water
    assert result.elevation_m == pytest.approx(expected, abs=TOL_M)


def test_pixel_center_matches_closed_form_exactly(dted: DtedMap, fixture_tiff: Path) -> None:
    # 在「像素中心」查詢 → 應精確等於解析式（僅剩 float32 儲存精度 ~0.001m 級）
    import rasterio as rio

    with rio.open(fixture_tiff) as ds:
        lng, lat = ds.xy(80, 60)
    expected = expected_elevation(lat, lng)
    assert expected is not None
    assert dted.get_elevation(lat, lng).elevation_m == pytest.approx(expected, abs=0.01)


def test_nodata_is_sea(dted: DtedMap) -> None:
    # lng > 121.4 的東側帶狀區為 nodata → 海面 0m / water=True（SPEC §4.1）
    result = dted.get_elevation(23.75, 121.45)
    assert result == ElevationResult(elevation_m=0.0, water=True)


def test_raw_nodata_value_never_leaks(dted: DtedMap) -> None:
    result = dted.get_elevation(23.9, 121.48)
    assert result.water
    assert result.elevation_m != NODATA  # -32768 不得外洩


# ---------------- 邊界與錯誤 ----------------


def test_bounds_property(dted: DtedMap) -> None:
    assert dted.bounds == pytest.approx((121.0, 23.5, 121.5, 24.0))


@pytest.mark.parametrize(
    ("lat", "lng"),
    [(25.0, 121.25), (23.75, 120.0), (23.75, 122.0), (0.0, 0.0)],
)
def test_out_of_bounds_raises(dted: DtedMap, lat: float, lng: float) -> None:
    with pytest.raises(OutOfBoundsError, match="覆蓋範圍外"):
        dted.get_elevation(lat, lng)


def test_nan_coordinate_rejected(dted: DtedMap) -> None:
    with pytest.raises(OutOfBoundsError, match="非法座標"):
        dted.get_elevation(float("nan"), 121.25)


def test_boundary_edges_are_inside(dted: DtedMap) -> None:
    # bbox 四邊（含角）屬於覆蓋範圍——不得拋出界（index 夾回有效像素）
    for lat, lng in [(23.5, 121.0), (24.0, 121.5), (23.5, 121.5), (24.0, 121.0)]:
        dted.get_elevation(lat, lng)  # 不拋即可


def test_missing_file_error_mentions_external_drive(tmp_path: Path) -> None:
    with pytest.raises(DtedFileNotFoundError, match="MATSO_DTED_PATH"):
        DtedMap.open(tmp_path / "nope.tiff")


# ---------------- 路徑設定（外接硬碟情境） ----------------


def test_env_var_overrides_dted_path(fixture_tiff: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """使用者需求：MATSO_DTED_PATH 手動指定地理資訊檔案路徑（如 /Volumes/外接硬碟/...）。"""
    monkeypatch.setenv("MATSO_DTED_PATH", str(fixture_tiff))
    settings = TerrainSettings()
    assert settings.dted_path == fixture_tiff
    with DtedMap.open_default(settings) as dted:
        assert not dted.get_elevation(PEAK_LAT, PEAK_LNG).water


def test_default_path_points_to_repo_data_dir() -> None:
    settings = TerrainSettings()
    assert settings.dted_path.name == "TW_ALL.tiff"
    assert settings.dted_path.parent.name == "data"


def test_open_default_missing_file_guides_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MATSO_DTED_PATH", "/Volumes/not-mounted/TW_ALL.tiff")
    with pytest.raises(DtedFileNotFoundError, match="外接硬碟"):
        DtedMap.open_default()


# ---------------- 確定性 ----------------


def test_repeated_queries_identical(dted: DtedMap) -> None:
    results = {dted.get_elevation(23.7, 121.2).elevation_m for _ in range(50)}
    assert len(results) == 1


def test_fixture_regeneration_is_deterministic(tmp_path: Path, fixture_tiff: Path) -> None:
    other = write_fixture(tmp_path / "again.tiff")
    with DtedMap.open(fixture_tiff) as a, DtedMap.open(other) as b:
        for lat, lng in [(23.75, 121.25), (23.6, 121.1), (23.9, 121.45)]:
            assert a.get_elevation(lat, lng) == b.get_elevation(lat, lng)


# ---------------- 真檔 benchmark（realdata：外接硬碟掛載 + MATSO_DTED_PATH 才跑） ----------------


@pytest.mark.realdata
def test_real_file_cold_start_under_30s(real_dted_path: Path) -> None:
    t0 = time.perf_counter()
    with DtedMap.open(real_dted_path) as dted:
        cold = time.perf_counter() - t0
        assert cold < 30.0, f"冷啟動 {cold:.1f}s ≥ 30s（SPEC §4.1）"
        # 取台灣中心點確認可讀
        dted.get_elevation(23.7, 121.0)


@pytest.mark.realdata
def test_real_file_query_p99_under_5ms(real_dted_path: Path) -> None:
    with DtedMap.open(real_dted_path) as dted:
        west, south, east, north = dted.bounds
        # 覆蓋範圍內均勻取 1000 點（確定性網格，不用亂數）
        latencies: list[float] = []
        n = 1000
        for i in range(n):
            lat = south + (north - south) * ((i * 37) % n) / n
            lng = west + (east - west) * ((i * 61) % n) / n
            t0 = time.perf_counter()
            dted.get_elevation(lat, lng)
            latencies.append(time.perf_counter() - t0)
        latencies.sort()
        p99 = latencies[int(n * 0.99) - 1]
        assert p99 < 0.005, f"p99 {p99 * 1000:.2f}ms ≥ 5ms（SPEC §4.3 SLA）"
