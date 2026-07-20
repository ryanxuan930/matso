"""CWA 解析器 + HTTP 來源錯誤處理（O5.2）。真 API 不測；解析器為純函數以 fixture 驗。"""

from __future__ import annotations

import httpx
import pytest
from weather.cwa import CwaHttpSource, parse_stations
from weather.live import CwaFetchError

_SAMPLE = {
    "records": {
        "Station": [
            {
                "GeoInfo": {
                    "Coordinates": [
                        {"CoordinateName": "TWD97", "StationLatitude": 0, "StationLongitude": 0},
                        {
                            "CoordinateName": "WGS84",
                            "StationLatitude": 23.5,
                            "StationLongitude": 121.0,
                        },
                    ]
                },
                "WeatherElement": {
                    "Now": {"Precipitation": 12.5},
                    "WindSpeed": 8.2,
                    "WindDirection": 135,
                    "VisibilityDescription": ">50 km",
                },
            },
            {  # 無效值（-99）→ 轉預設；缺座標的測站
                "GeoInfo": {
                    "Coordinates": [
                        {
                            "CoordinateName": "WGS84",
                            "StationLatitude": 24.0,
                            "StationLongitude": 121.5,
                        }
                    ]
                },
                "WeatherElement": {"Now": {"Precipitation": -99}, "WindSpeed": -99},
            },
            {"GeoInfo": {"Coordinates": []}, "WeatherElement": {}},  # 無 WGS84 → 略過
        ]
    }
}


def test_parse_stations_extracts_wgs84_and_values() -> None:
    stations = parse_stations(_SAMPLE)
    assert len(stations) == 2  # 第三個無座標被略過
    s0 = stations[0]
    assert (s0.lat, s0.lng) == (23.5, 121.0)  # 取 WGS84 而非 TWD97
    assert s0.raw.precipitation_mmhr == 12.5
    assert s0.raw.wind_ms == 8.2
    assert s0.raw.wind_dir_deg == 135
    assert s0.raw.visibility_m == pytest.approx(50000.0)  # >50 km → 50000 m


def test_parse_invalid_values_become_defaults() -> None:
    s1 = parse_stations(_SAMPLE)[1]  # 全 -99
    assert s1.raw.precipitation_mmhr == 0.0
    assert s1.raw.wind_ms == 0.0


def test_parse_empty_records() -> None:
    assert parse_stations({}) == []
    assert parse_stations({"records": {"Station": []}}) == []


def test_http_source_wraps_network_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def _boom(*_a: object, **_k: object) -> httpx.Response:
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "get", _boom)
    with pytest.raises(CwaFetchError, match="CWA 拉取失敗"):
        CwaHttpSource(api_key="x").fetch()


def test_http_source_wraps_http_status_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def _get(*_a: object, **_k: object) -> httpx.Response:
        return httpx.Response(500, request=httpx.Request("GET", "http://x"))

    monkeypatch.setattr(httpx, "get", _get)
    with pytest.raises(CwaFetchError):
        CwaHttpSource(api_key="x").fetch()


def test_http_source_success(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def _get(*_a: object, **_k: object) -> httpx.Response:
        return httpx.Response(200, json=_SAMPLE, request=httpx.Request("GET", "http://x"))

    monkeypatch.setattr(httpx, "get", _get)
    stations = CwaHttpSource(api_key="x").fetch()
    assert len(stations) == 2
    assert stations[0].raw.precipitation_mmhr == 12.5
