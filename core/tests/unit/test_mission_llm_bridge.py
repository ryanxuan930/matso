"""MISSION 令在 AI 側的三道關（WP-A2 卡 3）：G1 schema、G3 橋接、G4 禁射區。

三者**全部都是 fail-silent 的**：任何一道沒接好，症狀都是「LLM 好像不肯用任務令」
或更糟的「任務令穿過了護欄」，而不是任何一則錯誤訊息。
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from app.ai_loop.decider import OUTPUT_INSTRUCTION
from app.ai_loop.orders_bridge import UnitTargetLocator, tactical_order_to_request
from app.guardrails.gateway import _STRIKE_ORDER_TYPES
from app.orders.schemas import OrderType

_SCHEMA = json.loads(
    (pathlib.Path(__file__).resolve().parents[3] / "contracts" / "ai_output.schema.json").read_text(
        encoding="utf-8"
    )
)


def _order(**kw: Any) -> dict[str, Any]:
    return {"unit_id": "b1", **kw}


def _seize(**params: Any) -> dict[str, Any]:
    return _order(
        order_type="MISSION",
        mission_type="SEIZE",
        params={"objective": {"lat": 24.0, "lng": 121.0}, **params},
    )


# ---- G1：schema ----


def test_mission_passes_the_ai_output_schema() -> None:
    """**沒加進 enum 的話 G1 會擋掉整個決策，不只那一道令**——
    `gateway.evaluate` 在 schema 失敗時直接早退，OPFOR 重試兩次後 fallback 成零令。
    症狀是那個陣營整個不動，而不是「有一道令被拒」。
    """
    validator = Draft202012Validator({"$ref": "#/$defs/tactical_order", **_SCHEMA})
    errors = list(validator.iter_errors(_seize()))
    assert not errors, [e.message for e in errors]


def test_mission_type_enum_is_closed() -> None:
    validator = Draft202012Validator({"$ref": "#/$defs/tactical_order", **_SCHEMA})
    bad = _order(order_type="MISSION", mission_type="INVADE_MARS", params={})
    assert list(validator.iter_errors(bad)), "未知任務型應被 schema 擋下"


def test_output_instruction_teaches_every_mission_type() -> None:
    """詞彙表漏一種，LLM 就永遠不會用那一種。"""
    for mtype in ("SEIZE", "DEFEND", "SCREEN", "MOVE_MARCH"):
        assert mtype in OUTPUT_INSTRUCTION
    assert "MISSION" in OUTPUT_INSTRUCTION


def test_output_instruction_keeps_the_json_only_preamble() -> None:
    """`test_llm_faction_decider` 斷言這個字面片段——改寫開頭會讓它轉紅。"""
    assert "只**輸出一個 JSON" in OUTPUT_INSTRUCTION


# ---- G3：橋接 ----


def test_mission_order_bridges_into_a_request() -> None:
    """**沒接的話 `tactical_order_to_request` 回 None → G3 靜靜剔除 100% 的 MISSION 令**，
    而且同一個函式也是 submit 路徑：只補 G3 那一半的話，令會通過護欄然後落進
    `BridgeResult.skipped`，變成完全沒有痕跡的 no-op。"""
    req = tactical_order_to_request(_seize())
    assert req is not None
    assert req.order_type is OrderType.MISSION
    assert req.payload["mission_type"] == "SEIZE"
    assert req.payload["params"]["objective"] == {"lat": 24.0, "lng": 121.0}


def test_mission_without_type_is_not_bridged() -> None:
    assert tactical_order_to_request(_order(order_type="MISSION", params={})) is None


def test_mission_with_missing_params_still_bridges_and_fails_later() -> None:
    """params 的形狀由 `MissionPayload` 在 submit 時驗——**這裡不能靜靜丟掉**。

    丟掉的話，形狀錯誤會變成「令消失了」；讓它往下走，才會得到一個正規的 422 與留痕。
    """
    req = tactical_order_to_request(_order(order_type="MISSION", mission_type="SEIZE"))
    assert req is not None and req.payload["params"] == {}


# ---- G4：禁射區 ----


def test_mission_and_fire_mission_are_strike_order_types() -> None:
    """**原本只有 ENGAGE**。同一座標 ENGAGE 打不了、面射擊卻可以——那不是保護是繞道
    （BL-1 已在預檢端修過，AI 側的 G4 沒跟上）；而 SEIZE 會分解出對目標區內敵的 ENGAGE，
    母令不擋等於整條禁射區在任務級下令面前失效。"""
    assert {"ENGAGE", "FIRE_MISSION", "MISSION"} <= _STRIKE_ORDER_TYPES
    assert "MOVE" not in _STRIKE_ORDER_TYPES  # 開進禁射區不違規，打進去才是


class _Locator(UnitTargetLocator):
    def __init__(self) -> None:  # 不需要 DB——只驗座標路徑
        pass

    def _unit_latlng(self, unit_id: str) -> tuple[float, float] | None:
        return None


@pytest.mark.parametrize(
    ("params", "expect_cell"),
    [
        ({"objective": {"lat": 24.0, "lng": 121.0}}, True),
        ({"area": {"lat": 24.0, "lng": 121.0}}, True),
        ({"line": [{"lat": 24.0, "lng": 121.0}]}, False),  # SCREEN 不接戰
        ({"route": [{"lat": 24.0, "lng": 121.0}]}, False),  # 行軍只是移動
        ({}, False),
    ],
)
def test_locator_finds_the_mission_objective(params: dict[str, Any], expect_cell: bool) -> None:
    """**原本的註解宣稱支援 MISSION objective，實際只讀頂層 target_lat/lng**——
    MISSION 一律回 None，而 G4 對 None 的政策是**不擋**。等於一道打進禁射區的 SEIZE
    會直接穿過 G4。註解與行為不一致是最難查的那種錯。
    """
    cell = _Locator().locate(_order(order_type="MISSION", mission_type="SEIZE", params=params))
    assert (cell is not None) is expect_cell


def test_locator_ignores_params_on_non_mission_orders() -> None:
    """別的令型帶了 `params` 不該被當成任務目標。"""
    order = _order(order_type="MOVE", params={"objective": {"lat": 1, "lng": 2}})
    assert _Locator().locate(order) is None
