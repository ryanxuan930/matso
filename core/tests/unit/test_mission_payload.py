"""MISSION 令載荷驗證（WP-A2 卡 1）——壞掉的參數要在**收令的那一刻**就被擋下。

不驗的話，壞形狀會等到 Kernel tick 之中的分解時才炸；而 `kernel.run_tick` 對子系統的例外
**沒有任何防護**——一個 raise 會讓 runner 崩潰後被 `SimManager` 每 3 秒重建一次。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.orders.mission import MissionPayload, MissionType, SeizeParams
from app.orders.schemas import OrderType
from app.orders.validator import _PAYLOAD_MODELS


def test_mission_is_registered_in_the_payload_models() -> None:
    """**未登錄的令型會靜靜略過 payload 驗證**（`_parse_payload` 讓它以裸 dict 通過）——
    RECON/RESUPPLY 至今就是那樣。這條確保 MISSION 不會落進同一個洞。"""
    assert _PAYLOAD_MODELS[OrderType.MISSION] is MissionPayload


@pytest.mark.parametrize(
    ("mtype", "params"),
    [
        (MissionType.SEIZE, {"objective": {"lat": 24.0, "lng": 121.0}}),
        (MissionType.DEFEND, {"area": {"lat": 24.0, "lng": 121.0}, "area_radius_m": 300}),
        (MissionType.SCREEN, {"line": [{"lat": 24.0, "lng": 121.0}]}),
        (MissionType.MOVE_MARCH, {"route": [{"lat": 24.0, "lng": 121.0}], "spacing_km": 1.0}),
    ],
)
def test_valid_params_pass(mtype: MissionType, params: dict) -> None:  # type: ignore[type-arg]
    payload = MissionPayload(mission_type=mtype, params=params)
    assert payload.typed_params() is not None


@pytest.mark.parametrize(
    ("mtype", "params", "why"),
    [
        (MissionType.SEIZE, {}, "缺 objective"),
        (MissionType.SEIZE, {"objective": {"lat": 999, "lng": 121.0}}, "緯度超界"),
        (
            MissionType.SEIZE,
            {"objective": {"lat": 24.0, "lng": 121.0}, "objective_radius_m": 0},
            "半徑須 > 0",
        ),
        (MissionType.DEFEND, {"area_radius_m": 100}, "缺 area"),
        (MissionType.SCREEN, {"line": []}, "空的掩護線"),
        (MissionType.MOVE_MARCH, {"route": []}, "空的航路"),
        (
            MissionType.MOVE_MARCH,
            {"route": [{"lat": 24.0, "lng": 121.0}], "spacing_km": -1},
            "間距須 > 0",
        ),
    ],
)
def test_malformed_params_are_rejected_at_submit_time(
    mtype: MissionType,
    params: dict,
    why: str,  # type: ignore[type-arg]
) -> None:
    with pytest.raises(ValidationError):
        MissionPayload(mission_type=mtype, params=params)


def test_unknown_mission_type_is_rejected_by_the_enum() -> None:
    with pytest.raises(ValidationError):
        MissionPayload(mission_type="INVADE_MARS", params={})  # type: ignore[arg-type]


def test_axis_is_optional_and_defaults_empty() -> None:
    """沒有 axis 的 SEIZE 是合法的——直接朝目標去。"""
    p = SeizeParams.model_validate({"objective": {"lat": 24.0, "lng": 121.0}})
    assert p.axis == []
    assert p.objective_radius_m > 0
