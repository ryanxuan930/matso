"""任務級下令的載荷與階段（WP-A2）——**純資料 + 純函數，不碰 DB / 熱狀態**。

[IST160 p.4–5] 的核心論證：成熟系統下的是**任務**（Attack(axis, objective, limit lines)），
由準則庫展開成路徑/梯隊/交戰/脫離。MATSO 至今人與 LLM 都在微操三種低階令——
LLM 每個心跳要重新推理「下一步走哪」，呼叫頻率高、幻覺面積大。

**把分解交給符號層正是 Neuro-Symbolic 的本義**（SPEC_V2 §3 原則 3）：
LLM 只選任務型與參數，展開成低階令的是這裡的確定性 Python。

## 一套階段涵蓋四種任務型

各任務型只是走過 `MissionPhase` 的不同子集。另立四套階段機會讓 AAR 的任務時間軸
沒有共同語彙，而任務時間軸正是這張卡對 AAR 的主要交付。
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, field_validator


class MissionType(enum.StrEnum):
    SEIZE = "SEIZE"
    DEFEND = "DEFEND"
    SCREEN = "SCREEN"
    MOVE_MARCH = "MOVE_MARCH"


class MissionPhase(enum.StrEnum):
    """任務階段。**單調前進**——除了任何階段都可以掉進 FAILED。"""

    PLANNED = "PLANNED"
    MOVING = "MOVING"
    ENGAGING = "ENGAGING"
    CONSOLIDATING = "CONSOLIDATING"
    HOLDING = "HOLDING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


_TERMINAL = frozenset({MissionPhase.COMPLETE, MissionPhase.FAILED})


class LatLng(BaseModel):
    lat: float = Field(ge=-90.0, le=90.0)
    lng: float = Field(ge=-180.0, le=180.0)


class SeizeParams(BaseModel):
    """奪佔：沿 axis 機動 → 對目標區內敵接戰 → 佔領後轉守。"""

    objective: LatLng
    axis: list[LatLng] = Field(default_factory=list)
    objective_radius_m: float = Field(default=500.0, gt=0, le=50_000)


class DefendParams(BaseModel):
    """防守：就位 → 設 DEFENSE 姿態（WP-C1）→ 對進入射界之敵接戰。"""

    area: LatLng
    area_radius_m: float = Field(default=500.0, gt=0, le=50_000)
    orientation_deg: float | None = Field(default=None, ge=0.0, lt=360.0)


class ScreenParams(BaseModel):
    """掩護幕：沿線佔位 → 偵測回報但**不接戰** → 受壓後退。"""

    line: list[LatLng] = Field(min_length=1)


class MarchParams(BaseModel):
    """行軍序列：按序通過航路點。"""

    route: list[LatLng] = Field(min_length=1)
    spacing_km: float = Field(default=0.5, gt=0, le=50)


_PARAMS_MODEL: dict[MissionType, type[BaseModel]] = {
    MissionType.SEIZE: SeizeParams,
    MissionType.DEFEND: DefendParams,
    MissionType.SCREEN: ScreenParams,
    MissionType.MOVE_MARCH: MarchParams,
}


class MissionPayload(BaseModel):
    """MISSION 令載荷。`params` **在 submit 就依 mission_type 驗型**。

    不驗的話，壞掉的參數要等到分解時才炸——那時已經是 Kernel tick 之中，
    而 `kernel.run_tick` 對子系統的例外**沒有任何防護**（一個 raise 會讓 runner 崩潰後被
    每 3 秒重建一次）。壞令要在收令的那一刻就被擋下。
    """

    mission_type: MissionType
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def _params_shape(cls, v: dict[str, Any], info: Any) -> dict[str, Any]:
        mtype = (info.data or {}).get("mission_type")
        model = _PARAMS_MODEL.get(mtype) if mtype else None
        if model is None:
            return v
        model.model_validate(v)  # 形狀不合 → pydantic ValidationError → 422
        return v

    def typed_params(self) -> BaseModel:
        """已驗過的 params 轉成 typed model。分解器只吃這個，不吃裸 dict。"""
        return _PARAMS_MODEL[self.mission_type].model_validate(self.params)


@dataclass(frozen=True, slots=True)
class SubOrder:
    """分解出的一道低階令。**只是意圖，不是既成事實**——真正落庫走 `OrderService.submit`，
    所以子令一樣要過驗證、預檢、禁射區與 ROE。分解器不繞過任何閘門。"""

    order_type: str  # MOVE / ENGAGE / POSTURE
    payload: dict[str, Any]
    reason: str = ""  # 供 AAR 說明「為什麼這一步」


@dataclass(frozen=True, slots=True)
class MissionState:
    """任務在某一 tick 的狀態。**`waypoint_index` 是唯一的可變進度**——
    其餘一切都由 world_view 當場導出，不另存快照（存了就會與世界不同步）。"""

    phase: MissionPhase = MissionPhase.PLANNED
    waypoint_index: int = 0
    since_tick: int = 0


@dataclass(frozen=True, slots=True)
class MissionStep:
    """一次純狀態轉移的結果：下一個階段 + 這一步要送出的子令。"""

    state: MissionState
    orders: list[SubOrder] = field(default_factory=list)
    note: str = ""


# ---- 幾何（純函數；不引 h3/terrain——見模組說明）----

_EARTH_R_M = 6_371_000.0


def distance_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = p2 - p1
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_R_M * math.asin(math.sqrt(h))


__all__ = [
    "DefendParams",
    "LatLng",
    "MarchParams",
    "MissionPayload",
    "MissionPhase",
    "MissionState",
    "MissionStep",
    "MissionType",
    "ScreenParams",
    "SeizeParams",
    "SubOrder",
    "distance_m",
]
