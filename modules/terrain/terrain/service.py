"""TerrainService gRPC 實作（O2.5）——把 O2.1–O2.4 的純函數包成 gRPC。

領域邏輯（DTED 讀取、LOS、A*）全在既有純函數；本層只做 proto ↔ dataclass 轉換與資源守門：
- 高程/LOS/viewshed 需 DTED（外接硬碟或夾具）；缺 DTED → UNAVAILABLE。
- 路徑/cell 批次只需 hex 快取（parquet，不需硬碟）；缺快取 → UNAVAILABLE。
"""

from __future__ import annotations

from typing import NoReturn

import grpc
from matso_sdk._generated import terrain_pb2, terrain_pb2_grpc

from terrain.dted import DtedMap
from terrain.hexgrid import CellAttributes, HexGridCache
from terrain.los import Observer, check_los, get_viewshed
from terrain.pathfind import get_path


def _abort(context: grpc.ServicerContext, code: grpc.StatusCode, msg: str) -> NoReturn:
    """context.abort 一定拋例外；此包裝標註 NoReturn 讓型別收斂並保證不落空。"""
    context.abort(code, msg)
    raise AssertionError("grpc abort 未如預期拋例外")  # pragma: no cover


class TerrainService(terrain_pb2_grpc.TerrainServiceServicer):
    """DTED（高程/LOS/viewshed）與 hex 快取（路徑/cell）可各自缺席（外接硬碟情境）。"""

    def __init__(
        self,
        dted: DtedMap | None,
        cache: HexGridCache | None,
        viewshed_resolution: int = 8,
    ) -> None:
        self._dted = dted
        self._cache = cache
        self._res = viewshed_resolution

    def _require_dted(self, context: grpc.ServicerContext) -> DtedMap:
        if self._dted is None:
            _abort(context, grpc.StatusCode.UNAVAILABLE, "DTED 未載入（外接硬碟未掛載？）")
        return self._dted

    def _require_cache(self, context: grpc.ServicerContext) -> HexGridCache:
        if self._cache is None:
            _abort(context, grpc.StatusCode.UNAVAILABLE, "hex 快取未載入（尚未預計算？）")
        return self._cache

    def GetElevation(  # noqa: N802
        self, request: terrain_pb2.GetElevationRequest, context: grpc.ServicerContext
    ) -> terrain_pb2.GetElevationResponse:
        dted = self._require_dted(context)
        r = dted.get_elevation(request.position.lat, request.position.lng)
        return terrain_pb2.GetElevationResponse(elevation_m=r.elevation_m, water=r.water)

    def CheckLos(  # noqa: N802
        self, request: terrain_pb2.CheckLosRequest, context: grpc.ServicerContext
    ) -> terrain_pb2.CheckLosResponse:
        dted = self._require_dted(context)
        result = check_los(dted, _observer(request.observer), _observer(request.target))
        obstruction = terrain_pb2.LatLng()
        if result.obstruction_lat is not None and result.obstruction_lng is not None:
            obstruction = terrain_pb2.LatLng(lat=result.obstruction_lat, lng=result.obstruction_lng)
        return terrain_pb2.CheckLosResponse(
            visible=result.visible,
            obstruction_point=obstruction,
            fresnel_clearance=result.clearance_m,
        )

    def GetPath(  # noqa: N802
        self, request: terrain_pb2.GetPathRequest, context: grpc.ServicerContext
    ) -> terrain_pb2.GetPathResponse:
        cache = self._require_cache(context)
        try:
            result = get_path(cache, request.from_h3, request.to_h3, request.mobility_profile)
        except ValueError as exc:  # 未知 profile / 解析度不符 → 呼叫端錯誤
            _abort(context, grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        return terrain_pb2.GetPathResponse(
            h3_path=result.h3_path,
            total_cost=result.total_cost,
            eta_ticks=result.eta_ticks,
            reachable=result.reachable,
        )

    def GetCellBatch(  # noqa: N802
        self, request: terrain_pb2.GetCellBatchRequest, context: grpc.ServicerContext
    ) -> terrain_pb2.GetCellBatchResponse:
        cache = self._require_cache(context)
        found = cache.get_cell_batch(list(request.h3_index))
        return terrain_pb2.GetCellBatchResponse(cells=[_cell_info(c) for c in found.values()])

    def GetViewshed(  # noqa: N802
        self, request: terrain_pb2.GetViewshedRequest, context: grpc.ServicerContext
    ) -> terrain_pb2.GetViewshedResponse:
        dted = self._require_dted(context)
        try:
            visible = get_viewshed(dted, _observer(request.observer), request.radius_m, self._res)
        except ValueError as exc:  # 非正 radius
            _abort(context, grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        return terrain_pb2.GetViewshedResponse(visible_h3=visible)


def _observer(proto: terrain_pb2.Observer) -> Observer:
    return Observer(lat=proto.position.lat, lng=proto.position.lng, height_agl_m=proto.height_agl_m)


def _cell_info(c: CellAttributes) -> terrain_pb2.CellInfo:
    return terrain_pb2.CellInfo(
        h3_index=c.h3_index,
        center=terrain_pb2.LatLng(lat=c.center_lat, lng=c.center_lng),
        elevation_mean=c.elevation_mean,
        elevation_max=c.elevation_max,
        slope_deg=c.slope_deg,
        terrain_class=str(c.terrain_class),
        water=c.water,
        mobility_cost=c.mobility_cost,
    )
