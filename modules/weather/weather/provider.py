"""WeatherProvider 介面（O5.2）——SYNTHETIC 與 LIVE 共用的天氣來源抽象。

WeatherService/Plugin 只依賴此介面，不管背後是腳本插值（SYNTHETIC）或 CWA 拉取（LIVE）。
"""

from __future__ import annotations

from typing import Protocol

from weather.payload import WeatherPayload


class WeatherProvider(Protocol):
    def payload_at(self, sim_tick: int) -> WeatherPayload:
        """回傳某模擬 tick 的格網化天氣（含 stale 標記）。"""
        ...

    def is_stale(self) -> bool:
        """來源是否降級（LIVE 拉取失敗）；SYNTHETIC 恆 False。供插件健康狀態判定。"""
        ...
