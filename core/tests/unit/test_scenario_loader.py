"""Scenario loader（O7.1，SPEC §11.1 / §12.1）：載入 + 精確錯誤路徑 + factions/relations 驗證。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.factions import Relation
from app.models import TacticalUnit
from app.models.base import Base
from app.scenario import (
    ScenarioError,
    create_session_from_scenario,
    load_scenario_package,
)

_TUTORIAL = Path(__file__).resolve().parents[3] / "scenarios" / "examples" / "tutorial-platoon"


def test_load_official_tutorial_platoon() -> None:
    sc = load_scenario_package(_TUTORIAL)
    assert sc.name.startswith("Tutorial")
    assert sc.faction_ids == ["BLUE", "RED"]
    assert sc.faction_colors == {"BLUE": "#3b7dd8", "RED": "#d83b3b"}
    assert sc.relations.is_hostile("BLUE", "RED")
    # orbat：3 藍 + 2 紅，parent 參照有效
    assert len(sc.units) == 5
    assert {u.designation for u in sc.units if u.faction == "BLUE"} == {"B-CO", "B-1PLT", "B-2PLT"}
    # MSEL（O7.2）：2 條注入條目載入
    assert {e.id for e in sc.msel} == {"reinforce-blue-t30", "red-collapse"}
    assert len(sc.victory_conditions) == 2


def _write_pkg(tmp: Path, scenario: dict, orbats: dict[str, dict]) -> Path:
    (tmp / "orbat").mkdir(parents=True, exist_ok=True)
    (tmp / "scenario.yaml").write_text(yaml.safe_dump(scenario), encoding="utf-8")
    for name, ob in orbats.items():
        (tmp / "orbat" / name).write_text(yaml.safe_dump(ob), encoding="utf-8")
    return tmp


def _base_scenario() -> dict:
    return {
        "name": "T",
        "version": "1",
        "bbox": [120.0, 23.0, 121.0, 24.0],
        "mode": "REALTIME",
        "tick_rate_ms": 1000,
        "factions": [{"id": "BLUE"}, {"id": "RED"}],
        "victory_conditions": [{"faction": "BLUE", "condition": {}}],
    }


def test_missing_scenario_file(tmp_path: Path) -> None:
    with pytest.raises(ScenarioError, match=r"scenario\.yaml: 檔案不存在"):
        load_scenario_package(tmp_path)


def test_schema_error_has_precise_path(tmp_path: Path) -> None:
    sc = _base_scenario()
    del sc["bbox"]  # 缺必填
    _write_pkg(tmp_path, sc, {})
    with pytest.raises(ScenarioError, match=r"scenario\.yaml:.*bbox"):
        load_scenario_package(tmp_path)


def test_white_cell_reserved_rejected(tmp_path: Path) -> None:
    sc = _base_scenario()
    sc["factions"] = [{"id": "WHITE_CELL"}, {"id": "RED"}]
    _write_pkg(tmp_path, sc, {})
    with pytest.raises(ScenarioError, match=r"factions\[0\].id.*保留字"):
        load_scenario_package(tmp_path)


def test_duplicate_faction_rejected(tmp_path: Path) -> None:
    sc = _base_scenario()
    sc["factions"] = [{"id": "BLUE"}, {"id": "BLUE"}]
    _write_pkg(tmp_path, sc, {})
    with pytest.raises(ScenarioError, match=r"factions\[1\].id.*重複"):
        load_scenario_package(tmp_path)


def test_relation_unknown_faction_rejected(tmp_path: Path) -> None:
    sc = _base_scenario()
    sc["relations"] = [["BLUE", "YELLOW", "ALLIED"]]  # YELLOW 未宣告
    _write_pkg(tmp_path, sc, {})
    with pytest.raises(ScenarioError, match=r"relations\[0\]:.*YELLOW"):
        load_scenario_package(tmp_path)


def test_victory_unknown_faction_rejected(tmp_path: Path) -> None:
    sc = _base_scenario()
    sc["victory_conditions"] = [{"faction": "GREEN", "condition": {}}]
    _write_pkg(tmp_path, sc, {})
    with pytest.raises(ScenarioError, match=r"victory_conditions\[0\].faction.*GREEN"):
        load_scenario_package(tmp_path)


def test_orbat_unknown_parent_precise_path(tmp_path: Path) -> None:
    sc = _base_scenario()
    sc["factions"] = [{"id": "BLUE"}]
    sc["files"] = {"orbat": {"BLUE": "orbat/blue.yaml"}}
    orbat = {
        "faction": "BLUE",
        "units": [
            {"designation": "A", "unit_level": "COMPANY"},
            {"designation": "B", "unit_level": "PLATOON", "parent": "NOPE"},
        ],
    }
    _write_pkg(tmp_path, sc, {"blue.yaml": orbat})
    with pytest.raises(ScenarioError, match=r"orbat/blue\.yaml: units\[1\].parent.*NOPE"):
        load_scenario_package(tmp_path)


def test_relations_declared_allied(tmp_path: Path) -> None:
    sc = _base_scenario()
    sc["factions"] = [{"id": "BLUE"}, {"id": "RED"}, {"id": "YELLOW"}]
    sc["relations"] = [["BLUE", "YELLOW", "ALLIED"]]  # 其餘預設 HOSTILE
    _write_pkg(tmp_path, sc, {})
    sc_loaded = load_scenario_package(tmp_path)
    assert sc_loaded.relations.is_allied("BLUE", "YELLOW")
    assert sc_loaded.relations.relation("RED", "YELLOW") is Relation.HOSTILE  # 未宣告→HOSTILE


def test_create_session_from_scenario_builds_units_and_parents() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    loaded = load_scenario_package(_TUTORIAL)
    with factory() as db:
        sid = create_session_from_scenario(db, loaded, master_seed=42)
    with factory() as db:
        units = (
            db.execute(select(TacticalUnit).where(TacticalUnit.session_id == sid)).scalars().all()
        )
        assert len(units) == 5
        by_desig = {u.designation: u for u in units}
        assert by_desig["B-1PLT"].faction == "BLUE"
        # parent 連結建立（B-1PLT → B-CO）
        assert by_desig["B-1PLT"].parent_id == by_desig["B-CO"].id
        assert by_desig["B-CO"].parent_id is None
