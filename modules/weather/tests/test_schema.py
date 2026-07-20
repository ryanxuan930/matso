"""Weather 輸出對 contracts/weather_payload.schema.json 驗證（O5.1 驗收）。"""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from weather.effects import derive_effects
from weather.payload import RawWeather, WeatherCell, WeatherMode, WeatherPayload
from weather.synthetic import SyntheticWeather

_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contracts" / "weather_payload.schema.json"


def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")))


def _cell(raw: RawWeather, h3: str = "884d290d8bfffff") -> WeatherCell:
    return WeatherCell(h3_index=h3, raw=raw, effects=derive_effects(raw))


def test_synthetic_payload_conforms_to_schema() -> None:
    script = {
        "cells": {
            "884d290d8bfffff": {
                "keyframes": [
                    {"tick": 0, "precipitation_mmhr": 0, "wind_ms": 2},
                    {"tick": 100, "precipitation_mmhr": 25, "wind_ms": 15, "visibility_m": 800},
                ]
            }
        }
    }
    engine = SyntheticWeather.from_script(script)
    validator = _validator()
    for tick in (0, 25, 50, 100, 150):
        errors = sorted(validator.iter_errors(engine.payload_at(tick).to_dict()), key=str)
        assert not errors, f"tick {tick} 不符 schema：{errors}"


def test_extreme_values_conform_to_schema() -> None:
    # 暴雨強風極端天氣的 payload 仍須符合 schema（modifier 不越界）
    payload = WeatherPayload(
        issued_at_sim_tick=0,
        mode=WeatherMode.SYNTHETIC,
        stale=False,
        cells=(
            _cell(RawWeather(precipitation_mmhr=200, wind_ms=40, visibility_m=50, cloud_base_m=50)),
            _cell(RawWeather(), h3="884d290d8dfffff"),
        ),
    )
    assert not sorted(_validator().iter_errors(payload.to_dict()), key=str)
