"""SYNTHETIC 天氣：想定腳本關鍵影格插值（SPEC §5.1）。純函數、確定性。

腳本格式（JSON）：
    {"cells": {"<h3_index>": {"keyframes": [
        {"tick": 0,   "precipitation_mmhr": 0,  "wind_ms": 2,  "wind_dir_deg": 90, ...},
        {"tick": 120, "precipitation_mmhr": 25, "wind_ms": 15, "wind_dir_deg": 135, ...}
    ]}}}

任一 sim_tick 的原始值 = 相鄰兩關鍵影格線性插值（端點外夾住）；wind_dir 用最短角插值
（處理 360° 環繞）。之後經 effects.derive_effects 得效果係數，組成符合 schema 的 payload。
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from weather.effects import derive_effects
from weather.payload import (
    RawWeather,
    WeatherCell,
    WeatherMode,
    WeatherPayload,
)

_SCALAR_FIELDS = ("precipitation_mmhr", "wind_ms", "visibility_m", "cloud_base_m")


@dataclass(frozen=True, slots=True)
class _Keyframe:
    tick: int
    raw: RawWeather


class SyntheticWeather:
    """由關鍵影格腳本插值出任一 tick 的格網化天氣。"""

    def __init__(self, cells: dict[str, list[_Keyframe]]) -> None:
        self._cells = cells

    @classmethod
    def from_script(cls, script: dict[str, Any]) -> SyntheticWeather:
        raw_cells = script.get("cells")
        if not isinstance(raw_cells, dict) or not raw_cells:
            raise ValueError("synthetic 腳本缺 'cells' 物件")
        cells: dict[str, list[_Keyframe]] = {}
        for h3_index, spec in raw_cells.items():
            frames_raw = spec.get("keyframes") if isinstance(spec, dict) else None
            if not frames_raw:
                raise ValueError(f"cell {h3_index} 缺 keyframes")
            frames = sorted(
                (_Keyframe(tick=int(f["tick"]), raw=_raw_from(f)) for f in frames_raw),
                key=lambda k: k.tick,
            )
            cells[str(h3_index)] = frames
        return cls(cells)

    def cell_ids(self) -> list[str]:
        return sorted(self._cells)

    def interpolate(self, h3_index: str, sim_tick: int) -> RawWeather:
        frames = self._cells[h3_index]
        if sim_tick <= frames[0].tick:
            return frames[0].raw
        if sim_tick >= frames[-1].tick:
            return frames[-1].raw
        for a, b in pairwise(frames):
            if a.tick <= sim_tick <= b.tick:
                f = (sim_tick - a.tick) / (b.tick - a.tick)
                return _lerp_raw(a.raw, b.raw, f)
        return frames[-1].raw  # pragma: no cover — 已被端點夾住

    def payload_at(self, sim_tick: int) -> WeatherPayload:
        cells = tuple(
            WeatherCell(
                h3_index=h3,
                raw=(raw := self.interpolate(h3, sim_tick)),
                effects=derive_effects(raw),
            )
            for h3 in self.cell_ids()
        )
        return WeatherPayload(
            issued_at_sim_tick=sim_tick,
            mode=WeatherMode.SYNTHETIC,
            stale=False,  # SYNTHETIC 為確定性腳本，永不 stale
            cells=cells,
        )


def _raw_from(frame: dict[str, Any]) -> RawWeather:
    defaults = RawWeather()
    return RawWeather(
        precipitation_mmhr=float(frame.get("precipitation_mmhr", defaults.precipitation_mmhr)),
        wind_ms=float(frame.get("wind_ms", defaults.wind_ms)),
        wind_dir_deg=float(frame.get("wind_dir_deg", defaults.wind_dir_deg)),
        visibility_m=float(frame.get("visibility_m", defaults.visibility_m)),
        cloud_base_m=float(frame.get("cloud_base_m", defaults.cloud_base_m)),
    )


def _lerp(a: float, b: float, f: float) -> float:
    return a + (b - a) * f


def _lerp_angle(a: float, b: float, f: float) -> float:
    """最短角插值（處理 360° 環繞）。"""
    diff = ((b - a + 180.0) % 360.0) - 180.0
    return (a + diff * f) % 360.0


def _lerp_raw(a: RawWeather, b: RawWeather, f: float) -> RawWeather:
    values = {field: _lerp(getattr(a, field), getattr(b, field), f) for field in _SCALAR_FIELDS}
    return RawWeather(wind_dir_deg=_lerp_angle(a.wind_dir_deg, b.wind_dir_deg, f), **values)
