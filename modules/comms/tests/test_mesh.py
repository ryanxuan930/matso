"""Mesh 連通解算（O5.4，SPEC §6.1）——multi-hop 中繼 + 孤島 OFFLINE + faction 隔離。

鏈路狀態以注入的 obstruction_db 精確控制（真實中 VHF 鏈路多受地形遮蔽而非自由空間損耗
決定）。~1km 鏈路自由空間 margin≈60dB：obstruction 0→ONLINE、57→DEGRADED(≈3dB)、
200→OFFLINE。
"""

from __future__ import annotations

from collections.abc import Callable

from comms.link_budget import LinkState, Radio
from comms.mesh import CommsUnitInput, resolve_comms

_R = Radio(tx_power_dbm=30.0, antenna_gain_db=3.0, rx_sensitivity_dbm=-100.0, freq_mhz=150.0)
_OFFLINE_LOSS = 200.0
_DEGRADED_LOSS = 57.0  # ~1km 鏈路 → margin≈3dB（落在 [0,6]）


def _u(uid: str, lng_step: int, faction: str = "BLUE", *, hq: bool = False) -> CommsUnitInput:
    # 沿經度每步 ~1km（緯度 24° 附近）；避免超遠距離讓自由空間損耗自身成為變因
    return CommsUnitInput(uid, faction, 24.0, 121.0 + 0.01 * lng_step, _R, is_command_node=hq)


def _obstruction(pairs: dict[frozenset[str], float]) -> Callable[[str, str], float]:
    return lambda a, b: pairs.get(frozenset((a, b)), 0.0)


def _states(results: list) -> dict[str, LinkState]:
    return {r.unit_id: r.state for r in results}


def test_command_node_is_online_root() -> None:
    res = resolve_comms([_u("hq", 0, hq=True)])
    assert _states(res)["hq"] is LinkState.ONLINE


def test_direct_online_link_to_hq() -> None:
    res = resolve_comms([_u("hq", 0, hq=True), _u("a", 1)])
    assert _states(res)["a"] is LinkState.ONLINE


def test_multi_hop_relay_keeps_online() -> None:
    # hq—b 直接遮蔽（OFFLINE），但可經 a 全 ONLINE 中繼 → b 仍 ONLINE
    units = [_u("hq", 0, hq=True), _u("a", 1), _u("b", 2)]
    obst = _obstruction({frozenset(("hq", "b")): _OFFLINE_LOSS})
    st = _states(resolve_comms(units, obstruction_db=obst))
    assert st == {"hq": LinkState.ONLINE, "a": LinkState.ONLINE, "b": LinkState.ONLINE}


def test_multi_hop_bottleneck_degrades() -> None:
    # 至 hq 唯一路徑經一個 DEGRADED 跳（a—b）→ b 只能 DEGRADED（鏈路如最弱一環）
    units = [_u("hq", 0, hq=True), _u("a", 1), _u("b", 2)]
    obst = _obstruction(
        {frozenset(("hq", "b")): _OFFLINE_LOSS, frozenset(("a", "b")): _DEGRADED_LOSS}
    )
    st = _states(resolve_comms(units, obstruction_db=obst))
    assert st["a"] is LinkState.ONLINE
    assert st["b"] is LinkState.DEGRADED


def test_direct_degraded_link() -> None:
    units = [_u("hq", 0, hq=True), _u("a", 1)]
    obst = _obstruction({frozenset(("hq", "a")): _DEGRADED_LOSS})
    assert _states(resolve_comms(units, obstruction_db=obst))["a"] is LinkState.DEGRADED


def test_island_is_offline() -> None:
    # c 與所有人都被遮蔽 → 孤島 OFFLINE
    units = [_u("hq", 0, hq=True), _u("a", 1), _u("c", 2)]
    obst = _obstruction(
        {frozenset(("hq", "c")): _OFFLINE_LOSS, frozenset(("a", "c")): _OFFLINE_LOSS}
    )
    st = _states(resolve_comms(units, obstruction_db=obst))
    assert st["c"] is LinkState.OFFLINE
    assert st["a"] is LinkState.ONLINE


def test_weather_attenuation_can_sever_link() -> None:
    # 天氣 RF 衰減也能讓鏈路 OFFLINE（取兩端較差值）
    units = [_u("hq", 0, hq=True), _u("a", 1)]
    st = _states(resolve_comms(units, weather_attenuation_db=lambda u: 200.0 if u == "a" else 0.0))
    assert st["a"] is LinkState.OFFLINE


def test_jamming_severs_all_links() -> None:
    units = [_u("hq", 0, hq=True), _u("a", 1)]
    st = _states(resolve_comms(units, jamming_db=200.0))
    assert st["a"] is LinkState.OFFLINE  # anchor 仍 ONLINE（COP 根），a 被干擾孤立
    assert st["hq"] is LinkState.ONLINE


def test_factions_do_not_interconnect() -> None:
    # BLUE 單位無己方錨點，僅被 RED 指揮節點包圍 → 不可跨 faction 中繼 → OFFLINE
    units = [
        _u("red_hq", 0, "RED", hq=True),
        _u("blue_lone", 1, "BLUE"),
    ]
    st = _states(resolve_comms(units))
    assert st["red_hq"] is LinkState.ONLINE
    assert st["blue_lone"] is LinkState.OFFLINE  # 無 BLUE 錨點


def test_no_anchor_fallback_uses_best_direct() -> None:
    # faction 無指揮節點：退化為最佳直接鏈路；相連者 ONLINE、孤立者 OFFLINE
    units = [_u("a", 0), _u("b", 1), _u("lone", 2)]
    obst = _obstruction(
        {frozenset(("a", "lone")): _OFFLINE_LOSS, frozenset(("b", "lone")): _OFFLINE_LOSS}
    )
    st = _states(resolve_comms(units, obstruction_db=obst))
    assert st["a"] is LinkState.ONLINE
    assert st["b"] is LinkState.ONLINE
    assert st["lone"] is LinkState.OFFLINE  # 被遮蔽孤立，無錨點退化亦 OFFLINE


def test_no_anchor_fallback_degraded_direct() -> None:
    # 無錨點 + 唯一直接鏈路為 DEGRADED → 退化亦 DEGRADED（非 ONLINE、非孤島）
    units = [_u("a", 0), _u("b", 1)]
    obst = _obstruction({frozenset(("a", "b")): _DEGRADED_LOSS})
    st = _states(resolve_comms(units, obstruction_db=obst))
    assert st == {"a": LinkState.DEGRADED, "b": LinkState.DEGRADED}


def test_output_order_matches_input() -> None:
    units = [_u("z", 0, hq=True), _u("m", 1), _u("a", 2)]
    assert [r.unit_id for r in resolve_comms(units)] == ["z", "m", "a"]


def test_links_are_symmetric_and_populated() -> None:
    units = [_u("hq", 0, hq=True), _u("a", 1)]
    res = resolve_comms(units)
    hq = next(r for r in res if r.unit_id == "hq")
    a = next(r for r in res if r.unit_id == "a")
    assert [link.peer_id for link in hq.links] == ["a"]
    assert [link.peer_id for link in a.links] == ["hq"]
    assert hq.links[0].margin_db == a.links[0].margin_db  # 對稱鏈路
