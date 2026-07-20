"""CWA（中央氣象署）開放資料 LIVE 來源（O5.2）。

薄轉接：httpx 拉取 → 解析測站觀測 → StationObservation。任何 HTTP/網路/解析錯誤一律轉
`CwaFetchError`（由 LiveWeather 降級為 stale）。**離線/CI 不測真 API**——解析器 `parse_stations`
為純函數，以代表性 fixture 驗證；真實欄位對映（O-A0001-001 自動氣象站）於部署時以 API key 校準。
"""

from __future__ import annotations

from typing import Any

import httpx

from weather.live import CwaFetchError, StationObservation
from weather.payload import RawWeather

# CWA 開放資料自動氣象站資料集（現值）。實際 dataset id / 欄位於部署校準。
DEFAULT_CWA_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001"


class CwaHttpSource:
    """以 httpx 拉 CWA JSON 並解析為測站觀測。api_key 為 CWA 授權碼（env 注入，不寫死）。"""

    def __init__(
        self,
        api_key: str,
        url: str = DEFAULT_CWA_URL,
        timeout_s: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._url = url
        self._timeout_s = timeout_s

    def fetch(self) -> list[StationObservation]:
        try:
            resp = httpx.get(
                self._url,
                params={"Authorization": self._api_key, "format": "JSON"},
                timeout=self._timeout_s,
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:  # 網路/HTTP/JSON 解析
            raise CwaFetchError(f"CWA 拉取失敗：{exc}") from exc
        return parse_stations(data)


def parse_stations(data: dict[str, Any]) -> list[StationObservation]:
    """解析 CWA 自動氣象站 JSON → StationObservation（純函數；缺欄位以預設值填補）。

    預期結構（O-A0001-001）：records.Station[].{GeoInfo.Coordinates[], WeatherElement.*}。
    無法解析的測站略過；全數無效則回空清單（LiveWeather 會格網化為預設值，非 stale）。
    """
    stations = data.get("records", {}).get("Station", [])
    out: list[StationObservation] = []
    for st in stations:
        coord = _wgs84(st.get("GeoInfo", {}).get("Coordinates", []))
        if coord is None:
            continue
        lat, lng = coord
        el = st.get("WeatherElement", {})
        out.append(
            StationObservation(
                lat=lat,
                lng=lng,
                raw=RawWeather(
                    precipitation_mmhr=_num(el.get("Now", {}).get("Precipitation"), 0.0),
                    wind_ms=_num(el.get("WindSpeed"), 0.0),
                    wind_dir_deg=_num(el.get("WindDirection"), 0.0) % 360.0,
                    visibility_m=_visibility_m(el.get("VisibilityDescription")),
                    cloud_base_m=RawWeather().cloud_base_m,  # 自動站無雲底；預設
                ),
            )
        )
    return out


def _wgs84(coordinates: list[dict[str, Any]]) -> tuple[float, float] | None:
    for c in coordinates:
        if c.get("CoordinateName") == "WGS84":
            lat, lng = c.get("StationLatitude"), c.get("StationLongitude")
            if lat is not None and lng is not None:
                return float(lat), float(lng)
    return None


def _num(value: Any, default: float) -> float:
    """CWA 用 -99/-990 等表示無效值；轉為 default。"""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return default if n < 0 else n


def _visibility_m(description: Any) -> float:
    """能見度描述（>50km / 數字 km）→ 公尺。無法解析回良好能見度預設。"""
    default = RawWeather().visibility_m
    if not isinstance(description, str):
        return default
    text = description.replace(">", "").replace("＞", "").strip()
    try:
        return float(text.split()[0]) * 1000.0  # 假設單位 km
    except (ValueError, IndexError):
        return default
