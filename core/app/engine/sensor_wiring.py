"""感測器接線（#97）——把既有偵測模型接進活執行期 Kernel。

`intel/` 底下的偵測程式碼（sensor/sweep/store/sensor_system）早已寫好且有單元測試，缺的只是
「餵三個 lookup」：單位→感測器規格、單位→陣營、(觀測者,目標)→偵測環境。本模組即那層膠水，
角色與 `engage_wiring.py` 對交戰的角色相同（純接線；偵測數學仍在 `intel/sensor.py`）。

**紅線**：本層不裁決可見性——只收集環境係數交給 `detect_probability`；faction-scope 由
`intel/store.py` 強制（每筆 contact 綁 observer_faction）。

**內建基本目視**：既有 session 的單位身上沒有任何 SENSOR 類裝備，若只認裝備導出的感測器，
接線後仍會是 0 contact。故每個單位都有一份 organic observation 基準（人眼/望遠鏡級），
裝備有更好的感測器才覆蓋——同 #84 油料「惰性滿油」的精神：既有資料免遷移即可運作。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable

import h3
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adjudication.daylight import (
    LightLevel,
    concealment_modifier,
    optical_range_modifier,
)
from app.intel.seed_sensors import SEED_SENSORS
from app.intel.sensor import DetectionEnv, SensorProfile
from app.intel.sweep import SensorUnit, TargetUnit
from app.models.tables import EquipmentInstance, EquipmentTemplate, TacticalUnit
from app.weather import WeatherState, detection_weather_modifier

_LOG = logging.getLogger("engine.sensor_wiring")

# 感測器類裝備的 category（weaponeering.schema.json 的 `sensor` $def）。
_SENSOR_CATEGORY = "SENSOR"

# 觀測高度（公尺，離地）——與交戰 LOS 同量級：人員/車輛的觀測位置約 10m 內。
_OBS_HEIGHT_M = 10.0

# 內建基本目視（organic observation）：無感測裝備時每個單位仍具備的裸眼/望遠鏡級偵察能力。
# 直接沿用既有種子 `EO_DAY`（日間光學，4km）——不另立一組平行常數，避免同一件事有兩個真相；
# 該組值已對 weaponeering.schema.json 的 sensor $def 驗證過。日後調校移入設定（#93）。
INTRINSIC_OPTICAL = SensorProfile.from_base_stats(SEED_SENSORS["EO_DAY"])


class SensorResolver:
    """單位 → 感測器規格。裝備導出者優先（取 max_range 最遠的一件），否則用內建基本目視。

    活執行期建構一次（與 `WeaponResolver` 同紀律）：sweep 每 tick 都要查，不可每次打 DB。
    """

    def __init__(self, db: Session, session_id: str) -> None:
        self._by_unit: dict[str, SensorProfile] = {}
        self._faction_by_unit: dict[str, str] = {}
        self._build(db, session_id)

    def _build(self, db: Session, session_id: str) -> None:
        units = db.scalars(select(TacticalUnit).where(TacticalUnit.session_id == session_id)).all()
        for unit in units:
            self._faction_by_unit[unit.id] = unit.faction
            best: SensorProfile | None = None
            instances = db.scalars(
                select(EquipmentInstance).where(EquipmentInstance.owner_id == unit.id)
            ).all()
            for inst in instances:
                tmpl = db.get(EquipmentTemplate, inst.template_id)
                if tmpl is None or tmpl.category != _SENSOR_CATEGORY:
                    continue
                try:
                    profile = SensorProfile.from_base_stats(tmpl.base_stats)
                except (ValueError, KeyError, TypeError):
                    continue  # baseStats 壞 → 略過該件（不讓一件壞資料弄瞎整個單位）
                if best is None or profile.max_range_m > best.max_range_m:
                    best = profile
            # 無感測裝備 → 內建基本目視（見模組 docstring）。
            self._by_unit[unit.id] = best or INTRINSIC_OPTICAL

    def sensor_for(self, unit_id: str) -> SensorProfile | None:
        return self._by_unit.get(unit_id)

    def faction_for(self, unit_id: str) -> str:
        """單位陣營；查無（熱狀態有但 DB 無）→ 空字串，sweep 端會視為與任何人皆非同盟。"""
        return self._faction_by_unit.get(unit_id, "")

    def factions(self) -> list[str]:
        """本局有單位的陣營（確定性排序）。STATE_DIFF 每陣營投影用它列出觀測方（WP-C5）。"""
        return sorted(set(self._faction_by_unit.values()))


def make_detect_env(
    gateway: object | None = None,
    weather: WeatherState | None = None,
    light_for: Callable[[], LightLevel] | None = None,
) -> Callable[[SensorUnit, TargetUnit], DetectionEnv]:
    """回傳 env_for(observer, target) → DetectionEnv（地形 LOS + 天氣），比照 `make_engage_env`。

    - `los_clear`：有 terrain gateway 時查真實視線（雙方離地 10m）；**服務中斷 → True**
      （地形服務掛掉不該讓全場忽然變成瞎子——與交戰 LOS 同一退化紀律）。
    - `weather_modifier`：取觀測者所在 cell 的天氣修正，**依感測器種類**（光學看能見度、
      紅外看熱對比、雷達/聲學 v0 不受影響）；無天氣快照 → 1.0（晴天）。
    - `light_for`（WP-C4a）：**每次呼叫現讀當前光照**——sweep 跨 tick 重用同一個 env_for，
      快取一個等級會讓整局停在建立時的那一刻。無宣告 → None → 全部 1.0（既有局位元不變）。
    - 座標與快照給定即確定性 → replay 安全。
    """
    w_res = _weather_res(weather) if weather is not None else 8

    def env_for(observer: SensorUnit, target: TargetUnit) -> DetectionEnv:
        los_clear = True
        if gateway is not None:
            try:
                outcome = gateway.has_los(  # type: ignore[attr-defined]
                    (observer.lat, observer.lng, _OBS_HEIGHT_M),
                    (target.lat, target.lng, _OBS_HEIGHT_M),
                )
                los_clear = bool(outcome.visible)
            except Exception:
                los_clear = True  # 服務中斷 → 不致盲（安全退化）
        weather_mod = 1.0
        if weather is not None:
            try:
                effects = weather.effects_at(h3.latlng_to_cell(observer.lat, observer.lng, w_res))
                weather_mod = detection_weather_modifier(effects, observer.sensor.sensor_kind)
            except Exception:
                weather_mod = 1.0
        light_mod = 1.0
        conceal_mod = 1.0
        if light_for is not None:
            level = light_for()
            # 「我看多遠」看**觀測者自己的**夜視能力；「我多好被看到」是環境，對雙方成立。
            light_mod = optical_range_modifier(level, night_capable=_night_capable(observer))
            conceal_mod = concealment_modifier(level)
        return DetectionEnv(
            los_clear=los_clear,
            weather_modifier=weather_mod,
            concealment_modifier=conceal_mod,
            light_modifier=light_mod,
        )

    return env_for


def _night_capable(observer: SensorUnit) -> bool:
    """觀測者的感測器有沒有夜視。**只看裝備**（見 `adjudication/daylight.py` 的模組說明）。"""
    return bool(getattr(observer.sensor, "night_capable", False))


def _weather_res(weather: WeatherState) -> int:
    """天氣格網解析度（與 engage_wiring 同一取法；取不到用 res 8）。"""
    res = getattr(weather, "resolution", None)
    return int(res) if isinstance(res, int) and res > 0 else 8


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """球面距離（公尺）——供測試與診斷用；sweep 內部有自己的同式實作。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * 6_371_000.0 * math.asin(min(1.0, math.sqrt(a)))
