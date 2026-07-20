"""視線（LOS）與可視域（Viewshed）計算（SPEC §4.3、contracts terrain.proto CheckLos/GetViewshed）。

沿大圓線以固定步長（預設 30m）取樣 DTED，逐點比較「地形高程」與「視線高度」，
並以 4/3 等效地球半徑修正地球曲率（RF 標準；同時涵蓋大氣折射）。

視線高度：obs_h 與 tgt_h（各為地形高程 + AGL 天線高）沿距離線性內插。
曲率下沉：距觀測者 d1、距目標 d2 的取樣點，地球表面相對兩端連線的凸起
    bulge = d1 * d2 / (2 * R_eff)，R_eff = 4/3 × R_earth。
遮蔽判定：clearance(s) = los_h(s) − (terrain_h(s) + bulge(s))；任一內插點 clearance < 0 即遮蔽。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import h3

from terrain.dted import DtedMap

R_EARTH_M = 6_371_000.0
R_EFF_M = 4.0 / 3.0 * R_EARTH_M  # 4/3 等效地球半徑（RF / 大氣折射）
DEFAULT_STEP_M = 30.0


@dataclass(frozen=True, slots=True)
class Observer:
    """觀測者/目標（對應 proto Observer）。height_agl_m = 天線/觀測具離地高。"""

    lat: float
    lng: float
    height_agl_m: float = 0.0


@dataclass(frozen=True, slots=True)
class LosResult:
    """對應 proto CheckLosResponse。

    visible：是否互見。
    obstruction_lat/lng：visible=False 時第一個遮蔽點（自觀測者起）；否則 None。
    clearance_m：全線最小餘隙（視線高度 − 地形高度）。遮蔽時為負。供 RF 判斷第一菲涅耳區
        是否被切（真正的菲涅耳半徑計算需頻率，屬 comms 層 O5.4）。
    """

    visible: bool
    obstruction_lat: float | None
    obstruction_lng: float | None
    clearance_m: float


ElevationSampler = Callable[[float, float], float]


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R_EARTH_M * math.asin(math.sqrt(a))


def _slerp(lat1: float, lng1: float, lat2: float, lng2: float, f: float) -> tuple[float, float]:
    """大圓線上 fraction f（0=起點,1=終點）的點。"""
    la1, lo1, la2, lo2 = map(math.radians, (lat1, lng1, lat2, lng2))
    v1 = (math.cos(la1) * math.cos(lo1), math.cos(la1) * math.sin(lo1), math.sin(la1))
    v2 = (math.cos(la2) * math.cos(lo2), math.cos(la2) * math.sin(lo2), math.sin(la2))
    dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(v1, v2, strict=True))))
    omega = math.acos(dot)
    if omega < 1e-12:
        return lat1, lng1
    s1 = math.sin((1 - f) * omega) / math.sin(omega)
    s2 = math.sin(f * omega) / math.sin(omega)
    x, y, z = (s1 * a + s2 * b for a, b in zip(v1, v2, strict=True))
    norm = math.sqrt(x * x + y * y + z * z)
    x, y, z = x / norm, y / norm, z / norm
    return math.degrees(math.asin(max(-1.0, min(1.0, z)))), math.degrees(math.atan2(y, x))


def check_los(
    dted: DtedMap,
    observer: Observer,
    target: Observer,
    step_m: float = DEFAULT_STEP_M,
    sampler: ElevationSampler | None = None,
) -> LosResult:
    """兩點間視線判定。

    效能：預設把整條線的 bbox 一次讀入記憶體（line_sampler），沿線純陣列查值，
    避免逐點 rasterio I/O（p99<20ms 關鍵）。viewshed 會共用一個涵蓋整個半徑的 sampler。
    """
    if sampler is None:
        sampler = dted.line_sampler(
            min(observer.lng, target.lng),
            min(observer.lat, target.lat),
            max(observer.lng, target.lng),
            max(observer.lat, target.lat),
        )
    sample = sampler
    obs_h = sample(observer.lat, observer.lng) + observer.height_agl_m
    tgt_h = sample(target.lat, target.lng) + target.height_agl_m
    d = _haversine_m(observer.lat, observer.lng, target.lat, target.lng)
    if d <= step_m:  # 太近，中間無取樣點 → 互見
        return LosResult(True, None, None, math.inf)

    # 掃全線求「最小餘隙」及其位置：visible ⇔ 全線 terrain 不高於視線（min_clearance ≥ 0）。
    # clearance_m 回全線最小值（= fresnel_clearance），obstruction 回最小餘隙處（最嚴重遮蔽點）。
    n = math.ceil(d / step_m)
    min_clearance = math.inf
    worst_lat = worst_lng = 0.0
    for i in range(1, n):
        f = i / n
        s = f * d
        lat, lng = _slerp(observer.lat, observer.lng, target.lat, target.lng, f)
        terrain_h = sample(lat, lng)
        los_h = obs_h + (tgt_h - obs_h) * f
        bulge = s * (d - s) / (2.0 * R_EFF_M)
        clearance = los_h - (terrain_h + bulge)
        if clearance < min_clearance:
            min_clearance = clearance
            worst_lat, worst_lng = lat, lng
    if min_clearance < 0:
        return LosResult(False, worst_lat, worst_lng, min_clearance)
    return LosResult(True, None, None, min_clearance)


def get_viewshed(
    dted: DtedMap,
    observer: Observer,
    radius_m: float,
    resolution: int = 8,
    step_m: float = DEFAULT_STEP_M,
) -> list[str]:
    """回傳自 observer 半徑 radius_m 內、視線可達（目標為地面 AGL=0）的 h3 cell 清單。

    以 h3.grid_disk 涵蓋半徑內 cell，逐 cell 中心跑 check_los。半徑大時 cell 多——
    p99<200ms 於夾具驗證；真檔大半徑的優化（記憶體窗口共用射線）記 backlog。
    """
    if radius_m <= 0:
        raise ValueError(f"radius_m 必須 > 0，收到 {radius_m}")
    origin = h3.latlng_to_cell(observer.lat, observer.lng, resolution)
    edge_m = h3.average_hexagon_edge_length(resolution, unit="m")
    k = math.ceil(radius_m / edge_m) + 1
    # 整個半徑的 bbox 一次讀入記憶體，供所有射線共用（單次 I/O，p99<200ms 關鍵）。
    deg_pad = radius_m / 111_320.0 + edge_m / 111_320.0
    sampler = dted.line_sampler(
        observer.lng - deg_pad,
        observer.lat - deg_pad,
        observer.lng + deg_pad,
        observer.lat + deg_pad,
    )
    visible: list[str] = []
    for cell in h3.grid_disk(origin, k):
        lat, lng = h3.cell_to_latlng(cell)
        if _haversine_m(observer.lat, observer.lng, lat, lng) > radius_m:
            continue
        if not dted.contains(lat, lng):
            continue
        target = Observer(lat=lat, lng=lng, height_agl_m=0.0)
        if check_los(dted, observer, target, step_m=step_m, sampler=sampler).visible:
            visible.append(cell)
    return visible
