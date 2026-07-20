"""Mesh 連通 + 全體鏈路解算（SPEC §6.1）——確定性純函數。

單位間可 multi-hop 中繼；`networkx` 建圖判連通，孤島單位標記 OFFLINE。

連通模型（量化為兩級鏈路品質）：
- 指揮節點（`is_command_node`）為己方 COP 根，恆 ONLINE。
- 其餘單位：至任一同 faction 指揮節點——
  - 存在**全 ONLINE 邊**的路徑 → ONLINE；
  - 否則存在**不含 OFFLINE 邊**的路徑（含 DEGRADED 跳）→ DEGRADED（鏈路只如最弱一環）；
  - 皆無（孤島）→ OFFLINE。
- faction 無任何指揮節點 → 退化為各單位「最佳直接鏈路」（無 multi-hop），孤立→OFFLINE。

鏈路只在同 faction 內建立（己方 mesh；敵我不互聯）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import combinations

import networkx as nx

from comms.link_budget import (
    LinkState,
    Radio,
    haversine_m,
    link_margin_db,
    link_state_from_margin,
)


@dataclass(frozen=True, slots=True)
class CommsUnitInput:
    unit_id: str
    faction: str
    lat: float
    lng: float
    radio: Radio
    is_command_node: bool = False


@dataclass(frozen=True, slots=True)
class LinkResult:
    peer_id: str
    margin_db: float
    state: LinkState


@dataclass(frozen=True, slots=True)
class UnitResult:
    unit_id: str
    state: LinkState  # 最終狀態（含 mesh 連通判定）
    links: tuple[LinkResult, ...] = field(default_factory=tuple)  # 直接鏈路（診斷）


def resolve_comms(
    units: list[CommsUnitInput],
    *,
    obstruction_db: Callable[[str, str], float] = lambda _a, _b: 0.0,
    weather_attenuation_db: Callable[[str], float] = lambda _u: 0.0,
    jamming_db: float = 0.0,
) -> list[UnitResult]:
    """算全體單位的直接鏈路 + mesh 連通最終狀態。輸出順序＝輸入順序（確定性）。"""
    by_id = {u.unit_id: u for u in units}
    # 1) 同 faction 兩兩直接鏈路
    direct: dict[frozenset[str], LinkResult] = {}
    for a, b in combinations(units, 2):
        if a.faction != b.faction:
            continue
        dist = haversine_m(a.lat, a.lng, b.lat, b.lng)
        # 天氣衰減取兩端較差（保守）；遮蔽為該鏈路附加損耗
        atten = max(weather_attenuation_db(a.unit_id), weather_attenuation_db(b.unit_id))
        margin = link_margin_db(
            a.radio,
            b.radio,
            dist,
            obstruction_db=obstruction_db(a.unit_id, b.unit_id),
            weather_attenuation_db=atten,
            jamming_db=jamming_db,
        )
        direct[frozenset((a.unit_id, b.unit_id))] = LinkResult(
            peer_id="", margin_db=margin, state=link_state_from_margin(margin)
        )
    # 2) 每 faction 建圖解 mesh
    final: dict[str, LinkState] = {}
    for faction in {u.faction for u in units}:
        members = [u.unit_id for u in units if u.faction == faction]
        final.update(_resolve_faction(members, by_id, direct))
    # 3) 組裝輸出（含每單位直接鏈路，供白軍/AAR）
    results: list[UnitResult] = []
    for u in units:
        links = tuple(
            LinkResult(peer_id=other.unit_id, margin_db=lr.margin_db, state=lr.state)
            for other in units
            if other.unit_id != u.unit_id
            and (lr := direct.get(frozenset((u.unit_id, other.unit_id)))) is not None
        )
        results.append(UnitResult(unit_id=u.unit_id, state=final[u.unit_id], links=links))
    return results


def _resolve_faction(
    members: list[str],
    by_id: dict[str, CommsUnitInput],
    direct: dict[frozenset[str], LinkResult],
) -> dict[str, LinkState]:
    anchors = [m for m in members if by_id[m].is_command_node]

    # 兩級子圖：全 ONLINE 邊 / 不含 OFFLINE 邊（ONLINE+DEGRADED）
    g_online: nx.Graph = nx.Graph()
    g_reachable: nx.Graph = nx.Graph()
    g_online.add_nodes_from(members)
    g_reachable.add_nodes_from(members)
    for a, b in combinations(members, 2):
        lr = direct.get(frozenset((a, b)))
        if lr is None or lr.state is LinkState.OFFLINE:
            continue
        g_reachable.add_edge(a, b)
        if lr.state is LinkState.ONLINE:
            g_online.add_edge(a, b)

    out: dict[str, LinkState] = {}
    for m in members:
        if by_id[m].is_command_node:
            out[m] = LinkState.ONLINE  # COP 根
        elif not anchors:
            out[m] = _best_direct(m, members, direct)  # 無錨點退化
        elif _connected_to_any(g_online, m, anchors):
            out[m] = LinkState.ONLINE
        elif _connected_to_any(g_reachable, m, anchors):
            out[m] = LinkState.DEGRADED
        else:
            out[m] = LinkState.OFFLINE
    return out


def _connected_to_any(graph: nx.Graph, node: str, anchors: list[str]) -> bool:
    return any(a != node and nx.has_path(graph, node, a) for a in anchors)


def _best_direct(
    node: str, members: list[str], direct: dict[frozenset[str], LinkResult]
) -> LinkState:
    """無指揮錨點時的退化：取該單位最佳直接鏈路狀態（孤立→OFFLINE）。"""
    best = LinkState.OFFLINE
    for other in members:
        if other == node:
            continue
        lr = direct.get(frozenset((node, other)))
        if lr is None:
            continue
        if lr.state is LinkState.ONLINE:
            return LinkState.ONLINE
        if lr.state is LinkState.DEGRADED:
            best = LinkState.DEGRADED
    return best
