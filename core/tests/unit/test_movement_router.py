"""#82 Phase C：地形路徑規劃 + 任意點位起終點（非 hex 中心）+ 不可達退回直線。"""

from __future__ import annotations

import h3

from app.movement.router import ROUTE_RES, plan_route

# 非 hex 中心的「任意點位」起訖（使用者/AI 可指定任意經緯）。
_START = (23.7501234, 121.2001234)  # (lat, lng)
_DEST = (23.7604321, 121.2504321)


def _cells_between(start: tuple[float, float], dest: tuple[float, float]) -> list[str]:
    """造一條「起格→中間格→終格」的假 A* 路徑（沿直線取樣，去重保序）。"""
    out: list[str] = []
    for i in range(9):
        f = i / 8
        c = h3.latlng_to_cell(
            start[0] + (dest[0] - start[0]) * f, start[1] + (dest[1] - start[1]) * f, ROUTE_RES
        )
        if c not in out:
            out.append(c)
    return out


def _fake_path(cells: list[str], reachable: bool = True):
    def _fn(from_h3: str, to_h3: str, profile: str) -> tuple[list[str], bool]:
        return cells, reachable

    return _fn


def test_endpoints_are_exact_not_hex_centers() -> None:
    """任意點位 MUST：末點為精確目的地，不被吸附到格心。"""
    cells = _cells_between(_START, _DEST)
    r = plan_route(
        _fake_path(cells),
        start_lat=_START[0],
        start_lng=_START[1],
        dest_lat=_DEST[0],
        dest_lng=_DEST[1],
        profile="FOOT",
    )
    assert r.routed is True
    last_lng, last_lat = r.waypoints[-1]
    assert (last_lat, last_lng) == (_DEST[0], _DEST[1])  # 精確終點
    # 且不等於終點所在格的中心（證明沒有被吸附）。
    clat, clng = h3.cell_to_latlng(h3.latlng_to_cell(_DEST[0], _DEST[1], ROUTE_RES))
    assert (last_lat, last_lng) != (clat, clng)


def test_first_leg_starts_from_exact_position() -> None:
    """首格不取格心——單位由其當前精確位置出發（首段為部分格幾何段）。"""
    cells = _cells_between(_START, _DEST)
    r = plan_route(
        _fake_path(cells),
        start_lat=_START[0],
        start_lng=_START[1],
        dest_lat=_DEST[0],
        dest_lng=_DEST[1],
        profile="FOOT",
    )
    start_cell_center = h3.cell_to_latlng(cells[0])
    first_lng, first_lat = r.waypoints[0]
    # 第一個 waypoint 不是「起點所在格」的中心（否則等於先倒退回格心）。
    assert (first_lat, first_lng) != start_cell_center
    # waypoints 不含起點本身（執行器由當前位置推進）。
    assert (_START[1], _START[0]) not in r.waypoints


def test_intermediate_cells_are_followed() -> None:
    """中間格以格心為途經點 → 單位沿地形路徑走（而非直線穿越）。"""
    cells = _cells_between(_START, _DEST)
    r = plan_route(
        _fake_path(cells),
        start_lat=_START[0],
        start_lng=_START[1],
        dest_lat=_DEST[0],
        dest_lng=_DEST[1],
        profile="FOOT",
    )
    assert len(r.waypoints) == len(cells) - 2 + 1  # 中間格 + 精確終點
    mid_centers = [h3.cell_to_latlng(c) for c in cells[1:-1]]
    for (lng, lat), (clat, clng) in zip(r.waypoints[:-1], mid_centers, strict=True):
        assert (lat, lng) == (clat, clng)


def test_same_cell_is_direct_segment() -> None:
    """起訖同格（近距）→ 單段精確直線，不繞經格心。"""
    dest = (_START[0] + 0.00005, _START[1] + 0.00005)
    r = plan_route(
        _fake_path([]),
        start_lat=_START[0],
        start_lng=_START[1],
        dest_lat=dest[0],
        dest_lng=dest[1],
        profile="FOOT",
    )
    assert r.routed is False and r.reason == "same_cell"
    assert r.waypoints == [(dest[1], dest[0])]


def test_adjacent_cells_degenerate_to_direct() -> None:
    """路徑僅 2 跳（相鄰格）→ 無中間格 → 直接精確起訖單段。"""
    cells = [
        h3.latlng_to_cell(_START[0], _START[1], ROUTE_RES),
        h3.latlng_to_cell(_DEST[0], _DEST[1], ROUTE_RES),
    ]
    r = plan_route(
        _fake_path(cells),
        start_lat=_START[0],
        start_lng=_START[1],
        dest_lat=_DEST[0],
        dest_lng=_DEST[1],
        profile="FOOT",
    )
    assert r.routed is True
    assert r.waypoints == [(_DEST[1], _DEST[0])]


def test_unreachable_falls_back_to_straight() -> None:
    """A* 不可達（含超出 hex 快取範圍）→ 退回直線，不否決移動（避免長距離誤拒）。"""
    r = plan_route(
        _fake_path([], reachable=False),
        start_lat=_START[0],
        start_lng=_START[1],
        dest_lat=_DEST[0],
        dest_lng=_DEST[1],
        profile="FOOT",
    )
    assert r.routed is False and r.reason == "unreachable_fallback"
    assert r.waypoints == [(_DEST[1], _DEST[0])]


def test_path_service_error_falls_back() -> None:
    """地形服務中斷 → 退回直線（不凍結移動）。"""

    def _boom(from_h3: str, to_h3: str, profile: str) -> tuple[list[str], bool]:
        raise RuntimeError("terrain down")

    r = plan_route(
        _boom,
        start_lat=_START[0],
        start_lng=_START[1],
        dest_lat=_DEST[0],
        dest_lng=_DEST[1],
        profile="FOOT",
    )
    assert r.routed is False and r.reason.startswith("path_error:")
    assert r.waypoints == [(_DEST[1], _DEST[0])]
