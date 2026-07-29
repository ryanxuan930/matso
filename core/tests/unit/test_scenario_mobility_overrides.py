"""WP-B6 想定機動覆寫（`overrides/mobility_matrix.json`）。

兩件事要釘住：
1. **覆寫真的生效**，且是**局部**的（沒列到的鍵仍用出貨預設）。
2. **不得改變可通行性**——路徑規劃 A* 跑在 terrain 容器、讀它自己那份出貨矩陣，
   `GetPathRequest` 只帶 `{from_h3, to_h3, mobility_profile}`，看不到想定覆寫。
   改可通行會讓規劃端與執行端對「這條路走不走得通」意見不一致。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.models import WargameSession
from app.movement.mobility_matrix import MobilityRules, default_rules, step_cost
from app.movement.session_mobility import load_session_mobility_rules
from app.scenario import load_scenario_package
from app.scenario.loader import ScenarioError

_TUTORIAL = Path(__file__).resolve().parents[3] / "scenarios" / "examples" / "tutorial-platoon"
_OVERRIDE = "overrides/mobility_matrix.json"


def _copy(tmp_path: Path) -> Path:
    dest = tmp_path / "s"
    shutil.copytree(_TUTORIAL, dest)
    return dest


def _write_override(pkg: Path, patch: dict) -> None:  # type: ignore[type-arg]
    (pkg / "overrides").mkdir(exist_ok=True)
    (pkg / _OVERRIDE).write_text(json.dumps(patch), encoding="utf-8")


# --- 合併語義（純值物件）---


def test_override_is_partial_not_wholesale() -> None:
    base = default_rules()
    merged = base.merged({"profiles": {"FOOT": {"FOREST": 9.0}}})
    assert merged.step_cost("FOOT", "FOREST", 0.0) == 9.0
    # 同 profile 沒列到的 class、以及其他 profile，全部沿用預設
    assert merged.step_cost("FOOT", "URBAN", 0.0) == base.step_cost("FOOT", "URBAN", 0.0)
    assert merged.step_cost("TRACKED", "FOREST", 0.0) == base.step_cost("TRACKED", "FOREST", 0.0)
    assert merged.road_speed_factor("FOOT", "primary") == base.road_speed_factor("FOOT", "primary")


def test_override_can_touch_slope_and_road() -> None:
    merged = default_rules().merged(
        {
            "slope_penalty": {"FOOT": 0.0},
            "road": {"speed_factor_by_class": {"track": 0.1}, "usable_by_profile": {"BOAT": True}},
        }
    )
    # 坡度懲罰歸零 → 45 度與平地同成本
    assert merged.step_cost("FOOT", "GRASSLAND", 45.0) == merged.step_cost("FOOT", "GRASSLAND", 0.0)
    assert merged.road_speed_factor("FOOT", "track") == 0.1
    assert merged.road_speed_factor("BOAT", "track") == 0.1  # 原本 BOAT 不能用路


def test_empty_override_returns_the_same_defaults() -> None:
    base = default_rules()
    for patch in (None, {}, [], "nope"):
        assert base.merged(patch) is base


def test_module_level_helpers_still_use_shipped_defaults() -> None:
    """薄殼函數（既有呼叫端與 terrain 端鏡像用）必須維持出貨預設語義。"""
    assert step_cost("FOOT", "FOREST", 0.0) == default_rules().step_cost("FOOT", "FOREST", 0.0)
    assert step_cost("FOOT", "WATER", 0.0) is None  # 不可通行仍回 None


# --- 載入與驗證 ---


def test_tutorial_scenario_carries_the_override() -> None:
    sc = load_scenario_package(_TUTORIAL)
    assert sc.mobility_overrides["profiles"]["FOOT"]["FOREST"] == 2.0
    merged = default_rules().merged(sc.mobility_overrides)
    assert merged.step_cost("FOOT", "FOREST", 0.0) == 2.0


def test_missing_overrides_dir_is_fine(tmp_path: Path) -> None:
    pkg = _copy(tmp_path)
    shutil.rmtree(pkg / "overrides")
    assert load_scenario_package(pkg).mobility_overrides == {}


def test_override_making_terrain_passable_is_rejected(tmp_path: Path) -> None:
    """WHEELED 預設不可進 WETLAND；想定不得偷偷開放——A* 仍會判不可達並退回直線。"""
    pkg = _copy(tmp_path)
    _write_override(pkg, {"profiles": {"WHEELED": {"WETLAND": 2.0}}})
    with pytest.raises(ScenarioError, match="不得改變可通行性"):
        load_scenario_package(pkg)


def test_override_making_terrain_impassable_is_rejected(tmp_path: Path) -> None:
    """反向同理：A* 會規劃穿過去，單位到那格才 MOVE_BLOCKED 停死在半路。"""
    pkg = _copy(tmp_path)
    _write_override(pkg, {"profiles": {"FOOT": {"FOREST": -1}}})
    with pytest.raises(ScenarioError, match="不得改變可通行性"):
        load_scenario_package(pkg)


def test_override_may_add_a_combination_the_default_lacks(tmp_path: Path) -> None:
    """預設沒有的 (profile, class) 組合可新增——A* 對它本來就回 1.0，不存在分歧。"""
    pkg = _copy(tmp_path)
    _write_override(pkg, {"profiles": {"FOOT": {"GLACIER": 5.0}}})
    sc = load_scenario_package(pkg)
    assert default_rules().merged(sc.mobility_overrides).step_cost("FOOT", "GLACIER", 0.0) == 5.0


def test_malformed_override_is_rejected_with_a_path(tmp_path: Path) -> None:
    pkg = _copy(tmp_path)
    (pkg / _OVERRIDE).write_text("{not json", encoding="utf-8")
    with pytest.raises(ScenarioError, match="JSON 解析失敗"):
        load_scenario_package(pkg)


def test_override_violating_the_schema_is_rejected(tmp_path: Path) -> None:
    pkg = _copy(tmp_path)
    _write_override(pkg, {"profiles": {"FOOT": {"FOREST": "很慢"}}})
    with pytest.raises(ScenarioError, match="mobility_matrix"):
        load_scenario_package(pkg)


# --- 落地與讀回 ---


def test_session_override_persists_and_merges(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as db:
        ws = WargameSession(
            name="mob",
            master_seed=1,
            current_weather={},
            mobility_overrides={"profiles": {"FOOT": {"FOREST": 7.0}}},
        )
        db.add(ws)
        db.commit()
        rules = load_session_mobility_rules(db, ws.id)
    assert rules.step_cost("FOOT", "FOREST", 0.0) == 7.0
    assert rules.step_cost("FOOT", "URBAN", 0.0) == default_rules().step_cost("FOOT", "URBAN", 0.0)


def test_session_without_override_gets_the_shipped_defaults(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        ws = WargameSession(name="plain", master_seed=1, current_weather={})
        db.add(ws)
        db.commit()
        assert load_session_mobility_rules(db, ws.id) is default_rules()


def test_rules_are_a_frozen_value_object() -> None:
    """規則是值不是可變狀態——同一 core 行程同時跑 N 局，可變狀態會跨局污染。"""
    rules = MobilityRules(profiles={}, slope_penalty={}, road_factors={}, road_usable={})
    with pytest.raises(AttributeError):
        rules.profiles = {}  # type: ignore[misc]
