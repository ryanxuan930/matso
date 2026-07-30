"""兵科（`UnitBranch`）從想定一路到 API——**中性預設不可破**。

## 這一檔在防什麼

兵科是地圖符號的 2525C function ID 來源。在它存在之前，`UnitView` 根本沒有兵科欄位，
所以前端的 `functionId()` 恆回 `U-----`（通用框）——**每一個單位都畫成一樣的空框**。

新增這種欄位最容易犯的錯是**破壞中性預設**：既有想定沒有 `branch`，
若預設不是 UNKNOWN（＝通用框），所有既有局的符號外觀就會改變。
"""

from __future__ import annotations

import json
import pathlib

from jsonschema import Draft202012Validator

from app.factions import FactionRelations
from app.models.enums import UnitBranch
from app.scenario.dump import _orbat_dict
from app.scenario.loader import LoadedScenario, ScenarioUnit, _branch_of

_ORBAT_SCHEMA = pathlib.Path(__file__).resolve().parents[3] / "contracts" / "orbat.schema.json"


def _scenario(units: list[ScenarioUnit]) -> LoadedScenario:
    """只為了餵 `_orbat_dict` 的最小 LoadedScenario（其餘欄位取預設）。"""
    return LoadedScenario(
        name="t",
        version="0",
        mode="REALTIME",
        bbox=[0, 0, 1, 1],
        tick_rate_ms=1000,
        hex_resolution=8,
        aggregate_adjudication_level="PLATOON",
        faction_ids=["BLUE", "RED"],
        faction_colors={},
        relations=FactionRelations(),
        units=units,
    )


def test_unknown_is_the_neutral_default() -> None:
    """省略 branch → UNKNOWN。**這是既有想定零影響的保證。**"""
    u = ScenarioUnit(
        faction="BLUE", designation="B1", unit_level="SQUAD", lat=None, lng=None, parent=None
    )
    assert u.branch == "UNKNOWN"
    assert _branch_of("UNKNOWN") is UnitBranch.UNKNOWN


def test_a_typo_degrades_to_unknown_instead_of_killing_the_load() -> None:
    """打錯的兵科 → UNKNOWN，**不可以讓整份想定載不進來**。

    畫成通用框（看得出來沒設定）遠比整局開不起來好。
    """
    assert _branch_of("INFANTREE") is UnitBranch.UNKNOWN
    assert _branch_of("") is UnitBranch.UNKNOWN
    # 大小寫與空白要容忍（想定是人手寫的）
    assert _branch_of("  infantry ") is UnitBranch.INFANTRY


def test_branch_survives_an_export_import_roundtrip() -> None:
    """匯出要帶上 branch——**`fixed` 當年就是在這裡掉的**。

    症狀會是「把想定匯出再匯入，所有單位的兵科圖示就消失了」。
    """
    unit = ScenarioUnit(
        faction="BLUE",
        designation="B1",
        unit_level="COMPANY",
        lat=24.0,
        lng=121.0,
        parent=None,
        branch="ARMOR",
    )
    dumped = _orbat_dict(_scenario([unit]), "BLUE")
    assert dumped["units"][0]["branch"] == "ARMOR"
    # UNKNOWN 不輸出（預設，省略即等價）——維持既有想定的 diff 乾淨
    plain = ScenarioUnit(
        faction="BLUE",
        designation="B2",
        unit_level="COMPANY",
        lat=None,
        lng=None,
        parent=None,
    )
    assert "branch" not in _orbat_dict(_scenario([plain]), "BLUE")["units"][0]


def test_exported_orbat_still_conforms_to_the_schema() -> None:
    """加了欄位之後匯出結果仍要合 `orbat.schema.json`。"""
    schema = json.loads(_ORBAT_SCHEMA.read_text(encoding="utf-8"))
    unit = ScenarioUnit(
        faction="RED",
        designation="R1",
        unit_level="BATTALION",
        lat=24.0,
        lng=121.0,
        parent=None,
        branch="ARTILLERY",
    )
    errors = sorted(
        Draft202012Validator(schema).iter_errors(_orbat_dict(_scenario([unit]), "RED")), key=str
    )
    assert not errors, errors


def test_every_enum_value_is_declared_in_the_contract() -> None:
    """DB enum / Python enum / 契約 enum 三邊要一致。

    少一個值的症狀是：想定寫得出來、DB 存得下去，但契約驗證會擋掉匯出的想定。
    """
    schema = json.loads(_ORBAT_SCHEMA.read_text(encoding="utf-8"))
    declared = set(schema["properties"]["units"]["items"]["properties"]["branch"]["enum"])
    assert declared == {b.value for b in UnitBranch}


def test_defend_gives_engineers_an_emplace_order_but_not_infantry() -> None:
    """工兵抵達防區→構築障礙；非工兵只轉姿態。

    這條在 `branch` 存在之前**寫不出來**：分解器看得到的 `world_view` 只有階層
    （而且那一欄還被誤名為 `unit_type`），問不出「誰是工兵」。對步兵派 EMPLACE
    的下場是每個 tick 被預檢打回一次——所以這段功能一直掛在 Backlog 上。
    """
    from app.orders.decomposer import step
    from app.orders.mission import MissionPayload, MissionPhase, MissionState

    mission = MissionPayload(
        mission_type="DEFEND",
        params={"area": {"lat": 24.0, "lng": 121.0}, "area_radius_m": 500.0},
    )
    state = MissionState(phase=MissionPhase.MOVING, since_tick=0)
    at_area = {"unit_id": "u1", "lat": 24.0, "lng": 121.0, "posture": "MOVING"}

    engineer = step(mission, state, {**at_area, "branch": "ENGINEER"}, {}, tick=5)
    kinds = [o.order_type for o in engineer.orders]
    assert "POSTURE" in kinds and "ENGINEER" in kinds, kinds
    emplace = next(o for o in engineer.orders if o.order_type == "ENGINEER")
    assert emplace.payload["action"] == "EMPLACE"
    assert emplace.payload["obstacle_type"] == "WIRE"

    infantry = step(mission, state, {**at_area, "branch": "INFANTRY"}, {}, tick=5)
    assert [o.order_type for o in infantry.orders] == ["POSTURE"]
    # 沒有 branch（既有想定）→ 與步兵相同，行為不變
    unknown = step(mission, state, at_area, {}, tick=5)
    assert [o.order_type for o in unknown.orders] == ["POSTURE"]
