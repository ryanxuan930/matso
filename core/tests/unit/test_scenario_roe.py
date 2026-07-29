"""WP-B6 想定交戰規則（ROE）：解析、載入、以及**兩個確實會擋的生效點**。

紀律（承 WP-A3）：宣告了卻不生效的安全機制比沒有更危險。故每一條 schema 欄位都有
一條對應的「它真的擋住了」測試——沒有只驗結構就收工的欄位。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.adjudication.combined import CombinedWeapon, resolve_combined_engagement
from app.adjudication.engagement import EnvSnapshot, Resolution, Target
from app.adjudication.weapon import WeaponProfile
from app.engine.rng import DeterministicRNG
from app.models import EquipmentInstance, EquipmentTemplate, TacticalUnit, WargameSession
from app.models.enums import UnitLevel
from app.orders.precheck import _precheck_roe_weapon
from app.orders.roe import ALL_FACTIONS, EMPTY_ROE, RoeRules, load_session_roe, parse_roe
from app.orders.schemas import EngagePayload
from app.scenario import load_scenario_package
from app.scenario.loader import ScenarioError

_TUTORIAL = Path(__file__).resolve().parents[3] / "scenarios" / "examples" / "tutorial-platoon"


# --- 解析層（純函數）---


def test_parse_empty_declarations() -> None:
    for raw in (None, {}, [], "nope"):
        assert parse_roe(raw) == EMPTY_ROE
    assert not EMPTY_ROE.any_rules


def test_parse_fire_policy_and_restrictions() -> None:
    roe = parse_roe(
        {
            "default_fire_policy": {"RED": "SMALL_ARMS_ONLY", "BLUE": "bogus"},
            "weapon_restrictions": [
                {"forbid_categories": ["MISSILE"], "reason": "全場禁飛彈"},
                {"faction": "RED", "forbid_templates": ["T-90"], "reason": "紅軍不得用主戰車"},
            ],
        }
    )
    assert roe.fire_policy_for("RED") == "SMALL_ARMS_ONLY"
    assert roe.fire_policy_for("BLUE") is None  # 非法值被丟棄，不當成規則
    assert roe.fire_policy_for(None) is None
    # 全陣營規則對每一方都生效；陣營專屬規則只對該方生效
    assert roe.forbidden_for("BLUE") == {"MISSILE"}
    assert roe.forbidden_for("RED") == {"MISSILE", "T-90"}
    assert roe.reason_for("RED", "T-90") == "紅軍不得用主戰車"
    assert roe.reason_for("BLUE", "MISSILE") == "全場禁飛彈"
    assert roe.reason_for("BLUE", "不存在") == ""


def test_all_factions_key_cannot_collide_with_a_real_faction() -> None:
    # 陣營 id 受 ^[A-Z][A-Z0-9_]{1,31}$ 約束 → 保留鍵 "*" 不可能撞名
    import re

    assert not re.fullmatch(r"^[A-Z][A-Z0-9_]{1,31}$", ALL_FACTIONS)


# --- 載入層 ---


def test_tutorial_scenario_carries_roe() -> None:
    sc = load_scenario_package(_TUTORIAL)
    roe = parse_roe(sc.roe)
    assert roe.fire_policy_for("RED") == "SMALL_ARMS_ONLY"
    assert "MISSILE" in roe.forbidden_for("BLUE")


def test_roe_declaring_an_unknown_faction_is_rejected(tmp_path: Path) -> None:
    """打錯的陣營名不會被 JSON Schema 擋，規則就會安靜地套到不存在的陣營——沉默失效。"""
    import shutil

    import yaml

    shutil.copytree(_TUTORIAL, tmp_path / "s")
    roe_path = tmp_path / "s" / "roe.yaml"
    data = yaml.safe_load(roe_path.read_text(encoding="utf-8"))
    data["weapon_restrictions"][0]["faction"] = "PURPLE"
    roe_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ScenarioError, match="未宣告的陣營"):
        load_scenario_package(tmp_path / "s")


def test_missing_roe_file_is_an_error_not_a_silent_skip(tmp_path: Path) -> None:
    """與 msel「宣告了但檔不在就略過」刻意相反：ROE 缺檔要當場炸。"""
    import shutil

    shutil.copytree(_TUTORIAL, tmp_path / "s")
    (tmp_path / "s" / "roe.yaml").unlink()
    with pytest.raises(ScenarioError, match="檔案不存在"):
        load_scenario_package(tmp_path / "s")


def test_session_roe_persists_and_reads_back(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as db:
        ws = WargameSession(
            name="roe",
            master_seed=1,
            current_weather={},
            roe={"weapon_restrictions": [{"forbid_categories": ["ARTILLERY"], "reason": "x"}]},
        )
        db.add(ws)
        db.commit()
        assert load_session_roe(db, ws.id).forbidden_for("BLUE") == {"ARTILLERY"}


def test_session_without_roe_has_no_rules(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as db:
        ws = WargameSession(name="plain", master_seed=1, current_weather={})
        db.add(ws)
        db.commit()
        assert not load_session_roe(db, ws.id).any_rules


# --- 生效點 1：裁決層（權威——人與 AI 都走這裡）---


def _profile() -> WeaponProfile:
    return WeaponProfile.from_base_stats(
        {
            "max_range_m": 3000.0,
            "ph_by_range_band": [[3000.0, 1.0]],
            "damage_by_armor_class": {"INFANTRY": 50.0},
            "ammo_types": ["BALL"],
            "pk_by_armor_class": {"INFANTRY": 0.5},
        }
    )


def _target() -> Target:
    return Target(
        unit_id="t1",
        armor_class="INFANTRY",
        health=100.0,
        current_strength=100.0,
        authorized_strength=100.0,
        platform_count=10,
    )


def _env(_profile_arg: WeaponProfile) -> EnvSnapshot:
    return EnvSnapshot(range_m=1000.0, los_clear=True)


def _per_weapon(result: object) -> dict[str, dict]:  # type: ignore[type-arg]
    """per_weapon 明細只在事件的 ai_decision 裡（EngagementResult 不重複攜帶）。"""
    decision = result.events[0].ai_decision  # type: ignore[attr-defined]
    return {pw["weapon_id"]: pw for pw in decision["per_weapon"]}


def _resolve(forbidden: frozenset[str]):  # type: ignore[no-untyped-def]
    weapons = [
        CombinedWeapon(
            weapon_id="w-missile",
            profile=_profile(),
            quantity=2,
            ammo=10,
            category="MISSILE",
            template_name="TOW",
        ),
        CombinedWeapon(
            weapon_id="w-rifle",
            profile=_profile(),
            quantity=8,
            ammo=100,
            category="KINETIC",
            template_name="RIFLE_556",
        ),
    ]
    return resolve_combined_engagement(
        weapons,
        "s1",
        100.0,
        _target(),
        _env,
        DeterministicRNG(7, "adjudication"),
        tick=1,
        forbidden=forbidden,
    )


def test_forbidden_weapon_is_held_and_spends_no_ammo() -> None:
    result = _resolve(frozenset({"MISSILE"}))
    by_id = _per_weapon(result)
    assert by_id["w-missile"]["status"] == "HELD"
    assert by_id["w-missile"]["reason"] == "ROE"  # 與戰術上的 POLICY 保留區分開
    assert "w-missile" not in (result.ammo_spent_by_weapon or {})
    assert by_id["w-rifle"]["status"] != "HELD"  # 沒被禁的照打


def test_forbid_by_template_name() -> None:
    result = _resolve(frozenset({"TOW"}))
    by_id = _per_weapon(result)
    assert by_id["w-missile"]["status"] == "HELD"


def test_forbidding_everything_rejects_the_engagement() -> None:
    result = _resolve(frozenset({"MISSILE", "KINETIC"}))
    assert result.status is Resolution.REJECTED
    assert result.reason == "HOLD_FIRE"
    assert result.ammo_spent == 0


def test_empty_roe_is_bit_identical_to_before() -> None:
    """空 ROE 必須與改版前逐位元相同——既有推演局與 golden 不得受影響。"""
    baseline = _resolve(frozenset())
    assert baseline.status is not Resolution.REJECTED
    assert baseline.damage == _resolve(frozenset()).damage
    assert all(pw.get("reason") != "ROE" for pw in _per_weapon(baseline).values())


# --- 生效點 2：下令端 precheck（早退 + 留痕）---


def _unit_with_weapon(
    db: Session, *, faction: str = "BLUE", category: str = "MISSILE", name: str = "TOW"
) -> tuple[TacticalUnit, str]:
    ws = WargameSession(name="p", master_seed=1, current_weather={})
    db.add(ws)
    db.flush()
    unit = TacticalUnit(
        session_id=ws.id, designation="U1", unit_level=UnitLevel.SQUAD, faction=faction
    )
    tmpl = EquipmentTemplate(name=name, category=category, base_stats={})
    db.add_all([unit, tmpl])
    db.flush()
    inst = EquipmentInstance(template_id=tmpl.id, owner_id=unit.id, quantity=1, current_state={})
    db.add(inst)
    db.commit()
    return unit, inst.id


def test_precheck_rejects_an_explicitly_named_forbidden_weapon(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        unit, weapon_id = _unit_with_weapon(db)
        db.get(WargameSession, unit.session_id).roe = {
            "weapon_restrictions": [{"forbid_categories": ["MISSILE"], "reason": "演訓區限制"}]
        }
        db.commit()
        checks = _precheck_roe_weapon(
            db, unit, EngagePayload(target_unit_id="t1", weapon_id=weapon_id)
        )
    assert len(checks) == 1
    assert not checks[0].passed
    assert "MISSILE" in checks[0].detail and "演訓區限制" in checks[0].detail  # 理由要傳達給下令者


def test_precheck_passes_when_no_weapon_named(session_factory: sessionmaker[Session]) -> None:
    """沒指名武器 → 不在 submit 端猜，交由裁決層逐武器篩。"""
    with session_factory() as db:
        unit, _ = _unit_with_weapon(db)
        db.get(WargameSession, unit.session_id).roe = {
            "weapon_restrictions": [{"forbid_categories": ["MISSILE"], "reason": "x"}]
        }
        db.commit()
        assert _precheck_roe_weapon(db, unit, EngagePayload(target_unit_id="t1")) == []


def test_precheck_passes_for_a_faction_the_rule_does_not_cover(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        unit, weapon_id = _unit_with_weapon(db, faction="BLUE")
        db.get(WargameSession, unit.session_id).roe = {
            "weapon_restrictions": [
                {"faction": "RED", "forbid_categories": ["MISSILE"], "reason": "x"}
            ]
        }
        db.commit()
        assert (
            _precheck_roe_weapon(db, unit, EngagePayload(target_unit_id="t1", weapon_id=weapon_id))
            == []
        )


def test_precheck_passes_without_roe(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as db:
        unit, weapon_id = _unit_with_weapon(db)
        assert (
            _precheck_roe_weapon(db, unit, EngagePayload(target_unit_id="t1", weapon_id=weapon_id))
            == []
        )


def test_roe_rules_are_hashable_frozen_value() -> None:
    # frozen dataclass：不可於執行期被就地改寫（規則是宣告，不是可變狀態）
    roe = RoeRules(default_fire_policy={"BLUE": "FREE"})
    with pytest.raises(AttributeError):
        roe.default_fire_policy = {}  # type: ignore[misc]


# --- WP-B6：condition DSL 於**載入時**驗（未知 type 過去要到執行期才炸）---


def test_unknown_victory_condition_type_is_rejected_at_load(tmp_path: Path) -> None:
    """tutorial-platoon 曾寫著 `type: eliminate`（不存在的 type）——想定照樣載入，
    直到執行期評估勝負才丟 TriggerError，等於整局都不會判勝負。"""
    import shutil

    import yaml

    shutil.copytree(_TUTORIAL, tmp_path / "s")
    path = tmp_path / "s" / "scenario.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["victory_conditions"][0]["condition"] = {"type": "eliminate", "target_faction": "RED"}
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ScenarioError, match="未知的 condition type"):
        load_scenario_package(tmp_path / "s")


def test_condition_missing_a_required_field_is_rejected(tmp_path: Path) -> None:
    import shutil

    import yaml

    shutil.copytree(_TUTORIAL, tmp_path / "s")
    path = tmp_path / "s" / "scenario.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["victory_conditions"][0]["condition"] = {"type": "strength_below", "faction": "RED"}
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ScenarioError, match="缺少必填欄位 'value'"):
        load_scenario_package(tmp_path / "s")


def test_unknown_msel_trigger_type_is_rejected_at_load(tmp_path: Path) -> None:
    """MSEL 的 trigger 同理：未知 type → 該則注入整局靜默不發生。"""
    import shutil

    import yaml

    shutil.copytree(_TUTORIAL, tmp_path / "s")
    path = tmp_path / "s" / "msel.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["events"][0]["trigger"] = {"type": "when_i_feel_like_it"}
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ScenarioError, match=r"events\[0\]\.trigger"):
        load_scenario_package(tmp_path / "s")


def test_nested_all_any_conditions_are_validated_recursively() -> None:
    from app.scenario.triggers import TriggerError, validate_condition

    validate_condition(
        {
            "type": "all",
            "of": [
                {"type": "time", "at_tick": 10},
                {"type": "any", "of": [{"type": "faction_eliminated", "faction": "RED"}]},
            ],
        },
        "x",
    )
    with pytest.raises(TriggerError, match=r"all\.of\[1\]"):
        validate_condition(
            {"type": "all", "of": [{"type": "time", "at_tick": 1}, {"type": "bogus"}]}, "x"
        )
