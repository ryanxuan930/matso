"""LIVE 天氣（O5.2，SPEC §5.1/§5.2）——定期拉 CWA 開放資料 → 格網化 → LIVE payload。

**降級規約（MUST）**：CWA 來源失效時保留「最後有效值 + stale=true」；Core 收到 stale
超過 30 分鐘向 White Cell 告警（`stale_alert`）。恢復拉取成功即自動回 LIVE（stale=false）。

格網化：CWA 為測站點資料，以「最近測站」指派到各目標 H3 cell 中心（v0；克利金/反距離
加權於校準）。來源以 `CwaSource` Protocol 注入——測試用假件，不需真網路。

時鐘/拉取間隔為牆鐘（本模組為外部微服務，非模擬引擎，牆鐘合法）；now 以參數注入以利
確定性測試。
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import h3

from weather.effects import derive_effects
from weather.payload import RawWeather, WeatherCell, WeatherMode, WeatherPayload

_LOG = logging.getLogger("weather.live")
_EARTH_R_M = 6_371_000.0
_STALE_ALERT_S = 30 * 60  # SPEC §5.2：stale > 30 分鐘 → Core 告警（拉取間隔預設見 config）


class CwaFetchError(Exception):
    """CWA 來源拉取失敗（斷網 / API 失效）。"""


@dataclass(frozen=True, slots=True)
class StationObservation:
    """單一 CWA 測站觀測（點資料）。"""

    lat: float
    lng: float
    raw: RawWeather


class CwaSource(Protocol):
    def fetch(self) -> list[StationObservation]:
        """拉取當前 CWA 測站觀測；失敗拋 CwaFetchError。"""
        ...


class LiveWeather:
    """CWA LIVE 天氣提供者（WeatherProvider）。持有最後有效格網值與 stale 狀態。"""

    def __init__(
        self,
        source: CwaSource,
        target_cells: list[str],
        now: Callable[[], float],
        stale_alert_s: float = _STALE_ALERT_S,
    ) -> None:
        self._source = source
        self._target_cells = list(target_cells)
        self._now = now
        self._stale_alert_s = stale_alert_s
        self._cells: dict[str, RawWeather] = {}
        self._stale = True  # 首次成功拉取前為 stale（尚無資料）
        self._last_success: float | None = None

    def refresh(self) -> bool:
        """拉取一次；成功 → 更新格網 + stale=false；失敗 → 保留最後有效值 + stale=true。"""
        try:
            observations = self._source.fetch()
        except CwaFetchError:
            if not self._stale:
                _LOG.warning("CWA 拉取失敗 → 降級 stale（保留最後有效值）")
            self._stale = True
            return False
        self._cells = _gridify(observations, self._target_cells)
        if self._stale:
            _LOG.info("CWA 拉取成功 → 回 LIVE")
        self._stale = False
        self._last_success = self._now()
        return True

    def is_stale(self) -> bool:
        return self._stale

    def stale_duration_s(self) -> float:
        """降級持續時間（秒）；非 stale 或尚無成功紀錄回 0。"""
        if not self._stale or self._last_success is None:
            return 0.0
        return self._now() - self._last_success

    def stale_alert(self) -> bool:
        """是否達 Core 告警門檻（stale 超過 30 分鐘）。"""
        return self._stale and self.stale_duration_s() >= self._stale_alert_s

    def payload_at(self, sim_tick: int) -> WeatherPayload:
        cells = tuple(
            WeatherCell(
                h3_index=cell,
                raw=(raw := self._cells.get(cell, RawWeather())),
                effects=derive_effects(raw),
            )
            for cell in self._target_cells
        )
        return WeatherPayload(
            issued_at_sim_tick=sim_tick,
            mode=WeatherMode.LIVE,
            stale=self._stale,
            cells=cells,
        )


def _gridify(
    observations: list[StationObservation], target_cells: list[str]
) -> dict[str, RawWeather]:
    """把測站點觀測以最近測站指派到各目標 cell 中心。空觀測 → 各 cell 預設值。"""
    grid: dict[str, RawWeather] = {}
    for cell in target_cells:
        lat, lng = h3.cell_to_latlng(cell)
        if not observations:
            grid[cell] = RawWeather()
            continue
        nearest = min(observations, key=lambda o: _haversine_m(lat, lng, o.lat, o.lng))
        grid[cell] = nearest.raw
    return grid


def run_refresh_loop(live: LiveWeather, interval_s: float, stop: threading.Event) -> None:
    """背景拉取迴圈（牆鐘定期）。每 interval 拉一次，直到 stop 設置。"""
    while not stop.is_set():
        live.refresh()
        stop.wait(interval_s)


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin(math.radians(lat2 - lat1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    )
    return 2 * _EARTH_R_M * math.asin(math.sqrt(a))
