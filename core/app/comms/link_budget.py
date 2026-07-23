"""通訊鏈路預算 + 網狀連通（#33 / SPEC §6.1）——純同步純函數（HOW_TO §3、§4.2）。

鏈路預算（dB）：`margin = tx_power + gains − path_loss(距離,地形) − weather_atten − jamming`。
狀態映射（§6.1）：margin>6→ONLINE、0~6→DEGRADED、<0→OFFLINE。
網狀（§6.1）：單位間可 multi-hop 中繼；以連通分量判定——能經「可用鏈路（margin≥0）」連到任一
指揮節點者上線，全程強鏈（margin>6）→ONLINE，含弱鏈→DEGRADED，無法連到→OFFLINE（孤島）。

紅線：不碰時鐘/RNG/DB/RPC——地形遮蔽以布林旗標注入（由子系統查 terrain 後傳入）；確定性可重播。
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

# 沿用 O5.4 既有的鏈路狀態列舉（§6.2 戰術後果消費同一 LinkState），避免重複定義。
from app.comms.consequences import LinkState

# 狀態門檻（dB）。
_ONLINE_MARGIN_DB = 6.0
_USABLE_MARGIN_DB = 0.0
# 地形遮蔽（NLOS）額外路徑損耗（dB，繞射/遮蔽概估）。
_TERRAIN_NLOS_PENALTY_DB = 25.0
_EARTH_R_M = 6_371_000.0


@dataclass(frozen=True, slots=True)
class CommsProfile:
    """單位通訊裝備摘要（由 EquipmentTemplate/預設推導；純資料）。"""

    tx_power_dbm: float = 33.0  # 發射功率（~2W 手持 VHF）
    antenna_gain_db: float = 2.0  # 收發天線增益合計
    freq_mhz: float = 50.0  # 載波頻率（VHF）
    rx_sensitivity_dbm: float = -110.0  # 接收靈敏度（門檻）


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """兩點（[lng,lat]）大圓距離（公尺）。"""
    lng1, lat1 = a
    lng2, lat2 = b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_R_M * math.asin(min(1.0, math.sqrt(h)))


def fspl_db(distance_m: float, freq_mhz: float) -> float:
    """自由空間路徑損耗（dB）。近距（<1m）夾住避免 log 負無窮。"""
    d_km = max(distance_m, 1.0) / 1000.0
    return 20.0 * math.log10(d_km) + 20.0 * math.log10(max(freq_mhz, 1.0)) + 32.44


def _ground_excess_db(distance_m: float) -> float:
    """近地面雙徑（two-ray）超額損耗（dB）——1km 以外路徑損耗由 d² 轉向 d⁴（地面通訊真實化）。

    無此項時純 FSPL 讓 VHF 在數百公里仍上線（不合理）；加上後戰術 VHF 於約百公里量級進入
    OFFLINE，符合地面/地球曲率限制。1km 內不加（近距仍近自由空間）。
    """
    d_km = distance_m / 1000.0
    return 20.0 * math.log10(d_km) if d_km > 1.0 else 0.0


def link_margin_db(
    tx: CommsProfile,
    rx: CommsProfile,
    distance_m: float,
    *,
    obstructed: bool = False,
    weather_attenuation_db: float = 0.0,
    jamming_db: float = 0.0,
) -> float:
    """單向鏈路餘裕（dB）＝發射功率+增益 − 路徑損耗（+地形NLOS）− 天氣 − 干擾 − 接收靈敏度。"""
    path_loss = (
        fspl_db(distance_m, tx.freq_mhz)
        + _ground_excess_db(distance_m)
        + (_TERRAIN_NLOS_PENALTY_DB if obstructed else 0.0)
    )
    rx_power = tx.tx_power_dbm + tx.antenna_gain_db + rx.antenna_gain_db - path_loss
    return rx_power - weather_attenuation_db - jamming_db - rx.rx_sensitivity_dbm


def link_state(margin_db: float) -> LinkState:
    """鏈路餘裕 → 狀態（§6.1 門檻）。"""
    if margin_db > _ONLINE_MARGIN_DB:
        return LinkState.ONLINE
    if margin_db >= _USABLE_MARGIN_DB:
        return LinkState.DEGRADED
    return LinkState.OFFLINE


@dataclass(frozen=True, slots=True)
class CommsNode:
    """網狀節點：單位 + 座標 + 通訊 profile + 是否為指揮節點。"""

    unit_id: str
    lng: float
    lat: float
    profile: CommsProfile
    is_command: bool = False


def mesh_states(
    nodes: list[CommsNode],
    *,
    obstructed: dict[tuple[str, str], bool] | None = None,
    weather_attenuation_db: float = 0.0,
    jamming_db: float = 0.0,
) -> dict[str, LinkState]:
    """網狀連通 → 每個單位的通訊狀態（§6.1 multi-hop 中繼）。

    先算兩兩鏈路狀態（取雙向較差者），組圖：ONLINE 邊為強鏈、DEGRADED 邊為弱鏈、OFFLINE 不連。
    自任一指揮節點做 BFS——全程強鏈可達→ONLINE；經任一弱鏈可達→DEGRADED；不可達→OFFLINE（孤島）。
    無指揮節點時，退化為「以任一節點為錨」（避免全體 OFFLINE 的無意義結果）。
    """
    obstructed = obstructed or {}
    by_id = {n.unit_id: n for n in nodes}
    ids = [n.unit_id for n in nodes]
    # 兩兩邊狀態（無序對取雙向較差）。
    edge: dict[tuple[str, str], LinkState] = {}
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            na, nb = by_id[a], by_id[b]
            dist = haversine_m((na.lng, na.lat), (nb.lng, nb.lat))
            obs = obstructed.get((a, b), obstructed.get((b, a), False))
            m_ab = link_margin_db(
                na.profile,
                nb.profile,
                dist,
                obstructed=obs,
                weather_attenuation_db=weather_attenuation_db,
                jamming_db=jamming_db,
            )
            m_ba = link_margin_db(
                nb.profile,
                na.profile,
                dist,
                obstructed=obs,
                weather_attenuation_db=weather_attenuation_db,
                jamming_db=jamming_db,
            )
            st = link_state(min(m_ab, m_ba))
            if st is not LinkState.OFFLINE:
                edge[(a, b)] = st

    anchors = [n.unit_id for n in nodes if n.is_command] or (ids[:1] if ids else [])
    return _bfs_states(ids, edge, anchors)


def _bfs_states(
    ids: list[str], edge: dict[tuple[str, str], LinkState], anchors: list[str]
) -> dict[str, LinkState]:
    """自 anchors 做 BFS：記錄每個節點是否經過弱鏈（DEGRADED）到達。"""
    adj: dict[str, list[tuple[str, LinkState]]] = {i: [] for i in ids}
    for (a, b), st in edge.items():
        adj[a].append((b, st))
        adj[b].append((a, st))
    # best[id] = True 表示存在全強鏈路徑；False 表示只有含弱鏈路徑可達。
    reached: dict[str, bool] = {}
    dq: deque[tuple[str, bool]] = deque()
    for a in anchors:
        reached[a] = True  # 指揮節點自身視為強鏈上線
        dq.append((a, True))
    while dq:
        cur, strong = dq.popleft()
        for nxt, st in adj[cur]:
            nxt_strong = strong and st is LinkState.ONLINE
            if nxt not in reached or (nxt_strong and not reached[nxt]):
                reached[nxt] = nxt_strong
                dq.append((nxt, nxt_strong))
    out: dict[str, LinkState] = {}
    for i in ids:
        if i not in reached:
            out[i] = LinkState.OFFLINE
        else:
            out[i] = LinkState.ONLINE if reached[i] else LinkState.DEGRADED
    return out
