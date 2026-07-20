"""Terrain 插件整合測試（O2.5）：harness 起 gRPC server，各 RPC 對照純函數；健康狀態守門。"""

from __future__ import annotations

from pathlib import Path

import grpc
import h3
import pytest
from matso_sdk import HealthState, from_proto, run_plugin
from matso_sdk._generated import plugin_base_pb2, terrain_pb2, terrain_pb2_grpc
from terrain.config import TerrainSettings
from terrain.dted import DtedMap
from terrain.hexgrid import HexGridBuilder, HexGridCache
from terrain.los import Observer, check_los, get_viewshed
from terrain.plugin import TerrainPlugin, build_from_settings

_BBOX = (121.10, 23.60, 121.50, 23.90)
_RES = 7


@pytest.fixture(scope="module")
def dted(fixture_tiff: Path):  # type: ignore[no-untyped-def]
    with DtedMap.open(fixture_tiff) as d:
        yield d


@pytest.fixture(scope="module")
def cache(fixture_tiff: Path) -> HexGridCache:
    with DtedMap.open(fixture_tiff) as d:
        cells = {c.h3_index: c for c in HexGridBuilder(d).build_region(_BBOX, _RES)}
    return HexGridCache(cells)


def _stub(h) -> terrain_pb2_grpc.TerrainServiceStub:  # type: ignore[no-untyped-def]
    return terrain_pb2_grpc.TerrainServiceStub(h.channel)


# ---------------- manifest / health ----------------


def test_manifest(dted: DtedMap, cache: HexGridCache) -> None:
    with run_plugin(TerrainPlugin(dted, cache, _RES)) as h:
        m = h.manifest()
        assert m.name == "terrain"
        assert m.kind == "TERRAIN"
        assert "GetPath" in list(m.capabilities)


def test_health_healthy(dted: DtedMap, cache: HexGridCache) -> None:
    with run_plugin(TerrainPlugin(dted, cache, _RES)) as h:
        resp = h.base_stub().HealthCheck(plugin_base_pb2.HealthCheckRequest())
        assert from_proto(resp.state) is HealthState.HEALTHY


def test_health_degraded_without_dted(cache: HexGridCache) -> None:
    with run_plugin(TerrainPlugin(None, cache, _RES)) as h:
        resp = h.base_stub().HealthCheck(plugin_base_pb2.HealthCheckRequest())
        assert from_proto(resp.state) is HealthState.DEGRADED


def test_health_down_without_resources() -> None:
    with run_plugin(TerrainPlugin(None, None, _RES)) as h:
        resp = h.base_stub().HealthCheck(plugin_base_pb2.HealthCheckRequest())
        assert from_proto(resp.state) is HealthState.DOWN


# ---------------- 領域 RPC 對照純函數 ----------------


def test_get_elevation_matches(dted: DtedMap, cache: HexGridCache) -> None:
    lat, lng = 23.75, 121.25
    expected = dted.get_elevation(lat, lng)
    with run_plugin(TerrainPlugin(dted, cache, _RES)) as h:
        resp = _stub(h).GetElevation(
            terrain_pb2.GetElevationRequest(position=terrain_pb2.LatLng(lat=lat, lng=lng))
        )
        assert resp.elevation_m == pytest.approx(expected.elevation_m)
        assert resp.water == expected.water


def test_check_los_matches(dted: DtedMap, cache: HexGridCache) -> None:
    obs = Observer(lat=23.75, lng=121.12)
    tgt = Observer(lat=23.75, lng=121.38)
    expected = check_los(dted, obs, tgt)
    with run_plugin(TerrainPlugin(dted, cache, _RES)) as h:
        resp = _stub(h).CheckLos(
            terrain_pb2.CheckLosRequest(
                observer=terrain_pb2.Observer(
                    position=terrain_pb2.LatLng(lat=obs.lat, lng=obs.lng)
                ),
                target=terrain_pb2.Observer(position=terrain_pb2.LatLng(lat=tgt.lat, lng=tgt.lng)),
            )
        )
        assert resp.visible == expected.visible
        assert resp.fresnel_clearance == pytest.approx(expected.clearance_m)


def test_get_path_matches(dted: DtedMap, cache: HexGridCache) -> None:
    west = h3.latlng_to_cell(23.75, 121.13, _RES)
    east = h3.latlng_to_cell(23.75, 121.37, _RES)
    with run_plugin(TerrainPlugin(dted, cache, _RES)) as h:
        resp = _stub(h).GetPath(
            terrain_pb2.GetPathRequest(from_h3=west, to_h3=east, mobility_profile="FOOT")
        )
        assert resp.reachable
        assert resp.h3_path[0] == west
        assert resp.h3_path[-1] == east
        assert resp.total_cost > 0


def test_get_path_invalid_profile_aborts(dted: DtedMap, cache: HexGridCache) -> None:
    origin = h3.latlng_to_cell(23.75, 121.13, _RES)
    with run_plugin(TerrainPlugin(dted, cache, _RES)) as h, pytest.raises(grpc.RpcError) as ei:
        _stub(h).GetPath(
            terrain_pb2.GetPathRequest(from_h3=origin, to_h3=origin, mobility_profile="SUBMARINE")
        )
    assert ei.value.code() is grpc.StatusCode.INVALID_ARGUMENT


def test_get_cell_batch_matches(dted: DtedMap, cache: HexGridCache) -> None:
    # 取幾個已知在區域內的 h3 cell（含一個不存在者，驗證缺漏不回傳）
    present = [h3.latlng_to_cell(lat, 121.25, _RES) for lat in (23.72, 23.75, 23.78)]
    present = [h for h in present if cache.get_cell(h) is not None]
    absent = h3.latlng_to_cell(10.0, 100.0, _RES)  # 遠在區域外
    query = [*present, absent]
    with run_plugin(TerrainPlugin(dted, cache, _RES)) as h:
        resp = _stub(h).GetCellBatch(terrain_pb2.GetCellBatchRequest(h3_index=query))
        assert {c.h3_index for c in resp.cells} == set(present)  # absent 不出現
        for cell in resp.cells:
            src = cache.get_cell(cell.h3_index)
            assert src is not None
            assert cell.terrain_class == str(src.terrain_class)
            assert cell.mobility_cost == pytest.approx(src.mobility_cost)


def test_get_viewshed_matches(dted: DtedMap, cache: HexGridCache) -> None:
    obs = Observer(lat=23.75, lng=121.25, height_agl_m=5)
    expected = set(get_viewshed(dted, obs, 1500.0, _RES))
    with run_plugin(TerrainPlugin(dted, cache, _RES)) as h:
        resp = _stub(h).GetViewshed(
            terrain_pb2.GetViewshedRequest(
                observer=terrain_pb2.Observer(
                    position=terrain_pb2.LatLng(lat=obs.lat, lng=obs.lng), height_agl_m=5
                ),
                radius_m=1500.0,
            )
        )
        assert set(resp.visible_h3) == expected


def test_get_elevation_unavailable_without_dted(cache: HexGridCache) -> None:
    with run_plugin(TerrainPlugin(None, cache, _RES)) as h, pytest.raises(grpc.RpcError) as ei:
        _stub(h).GetElevation(
            terrain_pb2.GetElevationRequest(position=terrain_pb2.LatLng(lat=23.75, lng=121.25))
        )
    assert ei.value.code() is grpc.StatusCode.UNAVAILABLE


def test_get_path_unavailable_without_cache(dted: DtedMap) -> None:
    origin = h3.latlng_to_cell(23.75, 121.13, _RES)
    with run_plugin(TerrainPlugin(dted, None, _RES)) as h, pytest.raises(grpc.RpcError) as ei:
        _stub(h).GetPath(
            terrain_pb2.GetPathRequest(from_h3=origin, to_h3=origin, mobility_profile="FOOT")
        )
    assert ei.value.code() is grpc.StatusCode.UNAVAILABLE


# ---------------- build_from_settings：外接硬碟 fallback（使用者關鍵需求） ----------------


def test_build_from_settings_degraded_without_cache(fixture_tiff: Path, tmp_path: Path) -> None:
    # DTED 在（夾具）、無 hex 快取 → DEGRADED（仍可高程/LOS，路徑不可用）
    settings = TerrainSettings(dted_path=fixture_tiff, hex_cache_dir=tmp_path / "empty")
    plugin = build_from_settings(settings, resolution=7)
    state, _ = plugin.health()
    assert state is HealthState.DEGRADED


def test_build_from_settings_down_without_resources(tmp_path: Path) -> None:
    # 外接硬碟未掛載且未預計算 → DOWN（系統仍能啟動，不崩潰——fallback 需求）
    settings = TerrainSettings(
        dted_path=tmp_path / "nonexistent.tif", hex_cache_dir=tmp_path / "empty"
    )
    plugin = build_from_settings(settings, resolution=7)
    state, _ = plugin.health()
    assert state is HealthState.DOWN


def test_build_from_settings_loads_cache(fixture_tiff: Path, tmp_path: Path) -> None:
    # 預先寫入 res7 快取 → build_from_settings 應載入它（缺 DTED 仍 DEGRADED，但路徑可用）
    from terrain.hexgrid import write_parquet

    with DtedMap.open(fixture_tiff) as d:
        cells = HexGridBuilder(d).build_region(_BBOX, _RES)
        write_parquet(cells, tmp_path / "cache" / "res7.parquet")
    settings = TerrainSettings(dted_path=tmp_path / "nope.tif", hex_cache_dir=tmp_path / "cache")
    plugin = build_from_settings(settings, resolution=7)
    state, _ = plugin.health()
    assert state is HealthState.DEGRADED  # 有快取、無 DTED
