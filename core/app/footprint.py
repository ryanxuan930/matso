"""武器/雷達射界的地形裁切（viewshed fan）——純幾何 + 注入式 LOS 查詢（#11 ATAK）。

給定射源、方位扇形（方向/張角）與最大射程，逐方位射線向外查 LOS，取地形遮蔽（稜線/
defilade）之前的最大通視距離，組成「裁切後的射界多邊形」。真實地形（山脊/反斜面）會把
扇形啃出缺口，讓地圖上的攻擊範圍/雷達涵蓋不再是理想幾何扇形。

**紅線**：本模組純幾何、不碰時鐘/RNG/DB/RPC。LOS 查詢以 callable 注入（`LosRangeFn`）→
可單元測試、不綁 gRPC；給定 DEM + 相同輸入 → 相同輸出（決定性）→ replay 安全。RPC 編排
（呼叫 terrain gateway）留在 API 層。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

_EARTH_R_M = 6371000.0

# LOS 查詢注入：給 (obs_llh, tgt_llh)（各 lat, lng, 離地高 m）→ (visible, clear_range_m)。
# clear_range_m：不可見時為射源→遮蔽點的通視距離；可見時回要求距離（本模組會夾到 max）。
LosRangeFn = Callable[[tuple[float, float, float], tuple[float, float, float]], tuple[bool, float]]


@dataclass(frozen=True, slots=True)
class BearingSample:
    """單一方位的取樣結果——通視距離與是否受地形限制。"""

    bearing_deg: float
    range_m: float
    blocked: bool


@dataclass(frozen=True, slots=True)
class Footprint:
    """地形裁切後的射界：閉合多邊形環 + 逐方位取樣 + 是否有任一方位被裁切。"""

    ring: list[list[float]]  # [[lng, lat], …] 閉合環（首＝尾＝射源）
    samples: list[BearingSample]
    clipped: bool


def haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    """兩點大圓距離（公尺）——供把遮蔽點換算為通視距離。"""
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dphi = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_R_M * math.asin(min(1.0, math.sqrt(h)))


def dest_point(lat: float, lng: float, bearing_deg: float, dist_m: float) -> tuple[float, float]:
    """球面正解：由 (lat, lng) 沿方位角（北為 0、順時針）前進 dist_m → (lat, lng)。"""
    br = math.radians(bearing_deg)
    ang = dist_m / _EARTH_R_M
    lat1 = math.radians(lat)
    lng1 = math.radians(lng)
    sin_lat2 = math.sin(lat1) * math.cos(ang) + math.cos(lat1) * math.sin(ang) * math.cos(br)
    lat2 = math.asin(max(-1.0, min(1.0, sin_lat2)))
    lng2 = lng1 + math.atan2(
        math.sin(br) * math.sin(ang) * math.cos(lat1),
        math.cos(ang) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lng2)


def is_full_circle(arc_deg: float | None) -> bool:
    """張角是否等效於全圓（None / 非有限 / ≤0 / ≥360）。"""
    return arc_deg is None or not math.isfinite(arc_deg) or arc_deg <= 0 or arc_deg >= 360


def bearings(direction_deg: float | None, arc_deg: float | None, steps: int) -> list[float]:
    """扇形要取樣的方位角清單（北為 0、順時針）。

    - arc 無效 / ≥360 → 全圓：steps 個等距方位（不重複 0 與 360）。
    - 否則 → [dir-arc/2, dir+arc/2] 上 steps+1 個含端點方位。
    """
    n = max(3, steps)
    if is_full_circle(arc_deg):
        return [(360.0 * i) / n for i in range(n)]
    assert arc_deg is not None  # is_full_circle 已排除 None
    center = direction_deg if direction_deg is not None and math.isfinite(direction_deg) else 0.0
    start = center - arc_deg / 2.0
    return [start + (arc_deg * i) / n for i in range(n + 1)]


def compute_footprint(
    *,
    lng: float,
    lat: float,
    max_range_m: float,
    direction_deg: float | None,
    arc_deg: float | None,
    steps: int,
    observer_height_m: float,
    target_height_m: float,
    los_range: LosRangeFn,
) -> Footprint:
    """逐方位查 LOS，取地形遮蔽前的最大通視距離 → 裁切後的射界多邊形。

    每個方位以 max_range 端點查一次 LOS：可見 → 該方位滿射程；被地形擋 → 夾到遮蔽點距離。
    對全圓（雷達）以環閉合；對扇形（武器射向）以射源為頂點連成扇葉。
    """
    if max_range_m <= 0:
        return Footprint(ring=[], samples=[], clipped=False)
    full = is_full_circle(arc_deg)
    brs = bearings(direction_deg, arc_deg, steps)
    samples: list[BearingSample] = []
    for b in brs:
        tlat, tlng = dest_point(lat, lng, b, max_range_m)
        visible, clear_m = los_range((lat, lng, observer_height_m), (tlat, tlng, target_height_m))
        rng = max_range_m if visible else max(0.0, min(clear_m, max_range_m))
        samples.append(BearingSample(bearing_deg=b, range_m=rng, blocked=not visible))

    ring: list[list[float]] = []
    if not full:
        ring.append([lng, lat])  # 扇形頂點＝射源
    for s in samples:
        plat, plng = dest_point(lat, lng, s.bearing_deg, s.range_m)
        ring.append([plng, plat])
    # 閉合：全圓回到起始方位點；扇形回到射源頂點。
    if full and ring:
        ring.append(ring[0])
    elif ring:
        ring.append([lng, lat])
    clipped = any(s.blocked for s in samples)
    return Footprint(ring=ring, samples=samples, clipped=clipped)
