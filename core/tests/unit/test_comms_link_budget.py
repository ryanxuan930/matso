"""通訊鏈路預算 + 網狀連通（#33 / SPEC §6.1）——純函數：餘裕、狀態門檻、multi-hop 中繼。"""

from __future__ import annotations

from app.comms.link_budget import (
    CommsNode,
    CommsProfile,
    LinkState,
    fspl_db,
    link_margin_db,
    link_state,
    mesh_states,
)

_P = CommsProfile()


def test_fspl_increases_with_distance() -> None:
    assert fspl_db(10_000, 50) > fspl_db(1_000, 50) > fspl_db(100, 50)


def test_link_state_thresholds() -> None:
    assert link_state(10.0) is LinkState.ONLINE
    assert link_state(3.0) is LinkState.DEGRADED
    assert link_state(0.0) is LinkState.DEGRADED
    assert link_state(-5.0) is LinkState.OFFLINE


def test_margin_drops_with_distance_and_obstruction() -> None:
    near = link_margin_db(_P, _P, 1_000)
    far = link_margin_db(_P, _P, 30_000)
    blocked = link_margin_db(_P, _P, 1_000, obstructed=True)
    assert near > far
    assert near > blocked  # 地形遮蔽扣分


def test_jamming_and_weather_reduce_margin() -> None:
    base = link_margin_db(_P, _P, 5_000)
    jammed = link_margin_db(_P, _P, 5_000, jamming_db=30.0)
    wet = link_margin_db(_P, _P, 5_000, weather_attenuation_db=10.0)
    assert jammed < base and wet < base


def _node(uid: str, lng: float, lat: float, cmd: bool = False) -> CommsNode:
    return CommsNode(uid, lng, lat, _P, is_command=cmd)


def test_mesh_close_units_online() -> None:
    nodes = [_node("hq", 121.20, 23.75, cmd=True), _node("a", 121.201, 23.751)]
    states = mesh_states(nodes)
    assert states["hq"] is LinkState.ONLINE and states["a"] is LinkState.ONLINE


def test_mesh_isolated_unit_offline() -> None:
    # 指揮節點 + 一個極遠單位（>100km）→ 遠者孤島 OFFLINE。
    nodes = [_node("hq", 121.20, 23.75, cmd=True), _node("far", 123.5, 25.5)]
    states = mesh_states(nodes)
    assert states["hq"] is LinkState.ONLINE
    assert states["far"] is LinkState.OFFLINE


def test_mesh_multi_hop_relay() -> None:
    # hq —(~100km)— relay —(~100km)— edge：edge 距 hq ~200km 直連 OFFLINE，但經 relay 中繼可達。
    nodes = [
        _node("hq", 121.20, 23.75, cmd=True),
        _node("relay", 122.20, 23.75),
        _node("edge", 123.20, 23.75),
    ]
    # 先確認直連 hq→edge 不可用（否則測不到中繼）。
    from app.comms.link_budget import link_margin_db as _lm

    direct = _lm(_P, _P, 200_000)
    assert direct < 0  # 直連 OFFLINE
    states = mesh_states(nodes)
    assert states["edge"] is not LinkState.OFFLINE  # 經 relay 中繼可達


def test_mesh_per_call_deterministic() -> None:
    nodes = [_node("hq", 121.20, 23.75, cmd=True), _node("a", 121.25, 23.75)]
    assert mesh_states(nodes) == mesh_states(nodes)
