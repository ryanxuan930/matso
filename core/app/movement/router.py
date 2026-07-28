"""地形路徑規劃（#82 Phase C，SPEC_MOVEMENT §2.3）——把 A* hex 路徑轉為可執行的 waypoints。

核心：執行端不再直線穿越河流/山脈，而是沿 terrain A* 規劃的 hex 路徑前進（繞開不可通行地形）。

**任意點位起終點（SPEC §2.3 MUST）**：系統支援任意 lat/lng（非 hex 中心）的起訖點，**不得**被
`latlng_to_cell` 靜默吸附到格心。路徑組成：

    精確起點 →（A* 中間各格中心）→ 精確終點

- 首段：由單位**當前精確座標**出發（其所在格不取中心——單位已在格內某處）。
- 末段：終點為**精確目的地**（非其所在格中心）→ 單位最終停在使用者/AI 指定的точ點。
- 首末因此為「部分格」幾何段，距離/ETA/耗損按實際幾何長度計（既有 haversine 逐段推進自然成立）。
- 起訖同格 / 相鄰格（路徑退化為 ≤2 跳）→ 直接單段「精確起點→精確終點」，不強制繞經格心。

退化與韌性：A* 不可達（含超出 hex 快取範圍）→ **退回直線**並標記 `routed=False` + 原因，
由呼叫端記事件；**不因地形服務範圍不足而否決移動**（避免長距離誤拒，見 PROGRESS Backlog P4）。
純函數：路徑來源以 `path_fn` 注入（測試以假路徑，執行期為 terrain gRPC）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import h3

# A* 路徑查詢：(from_h3, to_h3, profile) → (h3_path, reachable)。不可達回 ([], False)。
PathFn = Callable[[str, str, str], tuple[list[str], bool]]

ROUTE_RES = 8  # 與戰術 hex grid / 地形取樣同解析度


@dataclass(frozen=True, slots=True)
class PlannedRoute:
    """規劃結果。`waypoints` 為 [(lng,lat), …]，**最後一點恆為精確目的地**（不含起點）。"""

    waypoints: list[tuple[float, float]]
    routed: bool  # True＝沿 A* 地形路徑；False＝退回直線（原因見 reason）
    reason: str = ""

    @property
    def hops(self) -> int:
        return len(self.waypoints)


def _straight(dest_lng: float, dest_lat: float, reason: str) -> PlannedRoute:
    return PlannedRoute(waypoints=[(dest_lng, dest_lat)], routed=False, reason=reason)


def plan_route(
    path_fn: PathFn,
    *,
    start_lat: float,
    start_lng: float,
    dest_lat: float,
    dest_lng: float,
    profile: str,
    resolution: int = ROUTE_RES,
) -> PlannedRoute:
    """規劃「精確起點 → 精確終點」的地形可行路徑（繞開不可通行地形）。

    回傳的 waypoints **不含起點**、末點為精確終點——與執行器既有的逐段（leg）推進相容。
    """
    from_h3 = h3.latlng_to_cell(start_lat, start_lng, resolution)
    to_h3 = h3.latlng_to_cell(dest_lat, dest_lng, resolution)
    if from_h3 == to_h3:
        # 同格內移動（近距）：直接精確直線，不繞經格心。
        return _straight(dest_lng, dest_lat, "same_cell")
    try:
        h3_path, reachable = path_fn(from_h3, to_h3, profile)
    except Exception as exc:  # 地形服務中斷 → 退回直線（不凍結移動）
        return _straight(dest_lng, dest_lat, f"path_error:{type(exc).__name__}")
    if not reachable or len(h3_path) < 2:
        # 不可達（真不可達或超出 hex 快取範圍）→ 退回直線，由呼叫端記錄原因。
        return _straight(dest_lng, dest_lat, "unreachable_fallback")
    # 丟掉首格（單位已在其中的精確位置）與末格（改用精確終點）→ 只取中間格心。
    mids = [h3.cell_to_latlng(c) for c in h3_path[1:-1]]
    waypoints = [(lng, lat) for lat, lng in mids]
    waypoints.append((dest_lng, dest_lat))
    return PlannedRoute(waypoints=waypoints, routed=True, reason="terrain_path")
