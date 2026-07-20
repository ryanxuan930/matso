"""天氣效果整合（O5.3，SPEC §5.3）——Core 端消費 weather 模組的 effects，映射為各裁決/
偵測/移動的 weather_modifier。

Core 不解讀氣象學：weather 模組已把原始值轉成效果係數（WeatherEffects），Core 只依 unit
所在 cell 的效果，填入 EnvSnapshot/DetectionEnv/AggregateEnv 的 weather_modifier（原佔位 1.0）。

純函數映射；WeatherState 由 weather gRPC client 每天氣 tick 更新（見 app.plugins.weather_client）。
weather 非硬依賴：無資料 / 插件 DOWN → CLEAR（所有 modifier=1.0，退化為無天氣影響）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CellEffects:
    """單一 cell 的天氣效果係數（Core 端鏡像 weather_payload 的 effects）。預設為晴天。"""

    rf_attenuation_db: float = 0.0
    mobility_modifier: float = 1.0
    sensor_optical_modifier: float = 1.0
    sensor_ir_modifier: float = 1.0
    uav_operability: bool = True
    rotary_wing_operability: bool = True
    artillery_dispersion_modifier: float = 1.0


CLEAR = CellEffects()  # 晴天：所有 modifier 中性


class WeatherState:
    """某天氣 tick 的格網化效果快照。查無 cell → CLEAR（無天氣影響）。"""

    def __init__(self, cells: dict[str, CellEffects] | None = None, stale: bool = False) -> None:
        self._cells = cells or {}
        self._stale = stale

    @classmethod
    def clear(cls) -> WeatherState:
        """全晴（weather 不可用時的降級預設）。"""
        return cls({}, stale=False)

    @property
    def stale(self) -> bool:
        return self._stale

    def effects_at(self, h3_index: str) -> CellEffects:
        return self._cells.get(h3_index, CLEAR)


# ---------------- 效果 → 各 env 的 weather_modifier（Core 的解讀，v0） ----------------


def engagement_weather_modifier(effects: CellEffects, indirect_fire: bool) -> float:
    """交戰命中的天氣修正。直瞄：能見度主導瞄準（optical）；間瞄：散佈增加 → 命中效益下降。"""
    if indirect_fire:
        return 1.0 / effects.artillery_dispersion_modifier  # 散佈越大 → 命中越低
    return effects.sensor_optical_modifier


def detection_weather_modifier(effects: CellEffects, sensor_kind: str) -> float:
    """偵測的天氣修正。光學/紅外依各自係數；雷達/聲學/電子偵蒐 v0 視為不受天氣影響。"""
    if sensor_kind == "OPTICAL":
        return effects.sensor_optical_modifier
    if sensor_kind == "IR":
        return effects.sensor_ir_modifier
    return 1.0


def aggregate_weather_modifier(effects: CellEffects) -> float:
    """聚合戰鬥的天氣修正：整體目標獲取/協同下降，以能見度為代理。"""
    return effects.sensor_optical_modifier


def movement_mobility_modifier(effects: CellEffects) -> float:
    """移動的天氣修正（越野機動阻力）。"""
    return effects.mobility_modifier


def uav_operable(effects: CellEffects) -> bool:
    return effects.uav_operability


def rotary_wing_operable(effects: CellEffects) -> bool:
    return effects.rotary_wing_operability
