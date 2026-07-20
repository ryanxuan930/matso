"""Weather 領域型別與標準化輸出（SPEC §5.2；契約 contracts/weather_payload.schema.json）。

frozen dataclass（內部領域物件）；`WeatherPayload.to_dict` 產生**符合 JSON schema** 的輸出。
Core 只消費 effects，不解讀原始氣象值。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any


class WeatherMode(enum.StrEnum):
    LIVE = "LIVE"
    SYNTHETIC = "SYNTHETIC"
    REPLAY = "REPLAY"


@dataclass(frozen=True, slots=True)
class RawWeather:
    """單一 cell 的原始氣象值（LIVE 來自 CWA、SYNTHETIC 來自腳本插值）。"""

    precipitation_mmhr: float = 0.0
    wind_ms: float = 0.0
    wind_dir_deg: float = 0.0
    visibility_m: float = 10000.0
    cloud_base_m: float = 3000.0


@dataclass(frozen=True, slots=True)
class WeatherEffects:
    """格網化效果係數（Core 消費的唯一內容）。範圍見 weather_payload.schema.json。"""

    rf_attenuation_db: float
    mobility_modifier: float
    sensor_optical_modifier: float
    sensor_ir_modifier: float
    uav_operability: bool
    rotary_wing_operability: bool
    artillery_dispersion_modifier: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "rf_attenuation_db": self.rf_attenuation_db,
            "mobility_modifier": self.mobility_modifier,
            "sensor_optical_modifier": self.sensor_optical_modifier,
            "sensor_ir_modifier": self.sensor_ir_modifier,
            "uav_operability": self.uav_operability,
            "rotary_wing_operability": self.rotary_wing_operability,
            "artillery_dispersion_modifier": self.artillery_dispersion_modifier,
        }


@dataclass(frozen=True, slots=True)
class WeatherCell:
    h3_index: str
    raw: RawWeather
    effects: WeatherEffects

    def to_dict(self) -> dict[str, Any]:
        return {
            "h3_index": self.h3_index,
            "precipitation_mmhr": self.raw.precipitation_mmhr,
            "wind_ms": self.raw.wind_ms,
            "wind_dir_deg": self.raw.wind_dir_deg,
            "visibility_m": self.raw.visibility_m,
            "cloud_base_m": self.raw.cloud_base_m,
            "effects": self.effects.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class WeatherPayload:
    issued_at_sim_tick: int
    mode: WeatherMode
    stale: bool
    cells: tuple[WeatherCell, ...]

    def to_dict(self) -> dict[str, Any]:
        """符合 contracts/weather_payload.schema.json 的 dict。"""
        return {
            "issued_at_sim_tick": self.issued_at_sim_tick,
            "mode": self.mode.value,
            "stale": self.stale,
            "cells": [c.to_dict() for c in self.cells],
        }
