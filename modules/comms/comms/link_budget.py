"""鏈路預算純函數（SPEC §6.1）——確定性、無隨機、可單元測試。

`link_margin_db = tx_power + gains − path_loss − weather_attenuation − jamming`
路徑損耗以自由空間（FSPL）為基底，加地形遮蔽附加損耗（Core 由 terrain 填）。
margin 映射鏈路狀態：`>6dB → ONLINE`、`0~6dB → DEGRADED`、`<0dB → OFFLINE`。
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass

# 狀態門檻（SPEC §6.1）；模組唯一權威，Core 端鏡像須一致。
ONLINE_MARGIN_DB = 6.0
DEGRADED_MARGIN_DB = 0.0

_EARTH_RADIUS_M = 6_371_000.0


class LinkState(enum.StrEnum):
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"


@dataclass(frozen=True, slots=True)
class Radio:
    """單位通訊裝備參數（SPEC §6.1，來自 EquipmentTemplate）。"""

    tx_power_dbm: float
    antenna_gain_db: float
    rx_sensitivity_dbm: float
    freq_mhz: float


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """兩 WGS84 點大圓距離（公尺）。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def free_space_path_loss_db(distance_m: float, freq_mhz: float) -> float:
    """自由空間路徑損耗（FSPL, dB）。d、f 皆 >0；同址（d≈0）以 1m 為地板避免 -inf。"""
    d_km = max(distance_m, 1.0) / 1000.0
    f = max(freq_mhz, 1e-6)
    return 20.0 * math.log10(d_km) + 20.0 * math.log10(f) + 32.44


def link_margin_db(
    tx: Radio,
    rx: Radio,
    distance_m: float,
    *,
    obstruction_db: float = 0.0,
    weather_attenuation_db: float = 0.0,
    jamming_db: float = 0.0,
) -> float:
    """鏈路餘裕（dB）＝發射端能量 − 各項損耗 − 接收靈敏度門檻。

    收發雙方視為對稱鏈路：以兩端天線增益總和、發射端功率、接收端靈敏度計算。
    obstruction/weather/jamming 為外部注入（terrain/weather/EW），保持本函數純。
    """
    path_loss = free_space_path_loss_db(distance_m, tx.freq_mhz)
    received_dbm = (
        tx.tx_power_dbm
        + tx.antenna_gain_db
        + rx.antenna_gain_db
        - path_loss
        - obstruction_db
        - weather_attenuation_db
        - jamming_db
    )
    return received_dbm - rx.rx_sensitivity_dbm


def link_state_from_margin(margin_db: float) -> LinkState:
    """SPEC §6.1 鏈路狀態映射（>6 ONLINE / 0–6 DEGRADED / <0 OFFLINE）。"""
    if margin_db > ONLINE_MARGIN_DB:
        return LinkState.ONLINE
    if margin_db >= DEGRADED_MARGIN_DB:
        return LinkState.DEGRADED
    return LinkState.OFFLINE
