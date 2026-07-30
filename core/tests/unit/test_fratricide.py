"""友軍誤傷語意（WP-C9）：開關、三條路徑的不對稱、FRATRICIDE 事件的受眾。

[JCATS-A p.5–6]：成熟系統「命令照輸入執行、後果照裁定」——錯誤的火力計畫打到自己的
補給點，照裁。系統的公正性來自它不替下令者遮羞。
"""

from __future__ import annotations

import pytest

from app.adjudication.fratricide import blocks_engagement, fratricide_victims, is_friendly
from app.factions.relations import FactionRelations, Relation

_ALLIED = FactionRelations([("BLUE", "GREEN", Relation.ALLIED)])
_NEUTRAL = FactionRelations([("BLUE", "GREEN", Relation.NEUTRAL)])


# ---- 純語義 ----


def test_friendly_means_the_relations_matrix_not_string_equality() -> None:
    """**這條記錄的是一個真的 bug**：`area_fire` 原本用 `faction == shooter_faction`
    判 `friendly_losses`，於是聯軍誤傷（BLUE 打到 GREEN 盟軍）不會被標成友軍傷亡——
    AAR 上看起來像正常戰果。"""
    assert is_friendly(_ALLIED, "BLUE", "BLUE") is True
    assert is_friendly(_ALLIED, "BLUE", "GREEN") is True  # ← 字串比較會漏掉這個
    assert is_friendly(_ALLIED, "BLUE", "RED") is False


def test_hostile_targets_are_never_blocked() -> None:
    for allow in (False, True):
        assert blocks_engagement(_ALLIED, "BLUE", "RED", allow_fratricide=allow)[0] is False


def test_allies_are_blocked_by_default() -> None:
    blocked, reason = blocks_engagement(_ALLIED, "BLUE", "GREEN", allow_fratricide=False)
    assert blocked is True and "非敵對" in reason


def test_the_switch_opens_allies_and_own_faction() -> None:
    for target in ("GREEN", "BLUE"):
        blocked, reason = blocks_engagement(_ALLIED, "BLUE", target, allow_fratricide=True)
        assert blocked is False
        assert "誤傷" in reason, "放行但**一定要留話**——這條路徑存在的意義就是「你確定嗎」"


def test_the_switch_does_not_legalise_attacking_neutrals() -> None:
    """原本那條是 `not is_hostile(...)`，一個分支同時涵蓋自己陣營、ALLIED 與 NEUTRAL。
    開關若直接套上去，會**無聲地**把「攻擊中立方」一起放行——那是完全不同的一件事。"""
    for allow in (False, True):
        blocked, _ = blocks_engagement(_NEUTRAL, "BLUE", "GREEN", allow_fratricide=allow)
        assert blocked is True, "中立方不在 allow_fratricide 的範圍內"


def test_victims_are_picked_by_relation_not_by_name() -> None:
    victims = fratricide_victims(_ALLIED, "BLUE", {"BLUE": 3.0, "GREEN": 2.0, "RED": 9.0})
    assert set(victims) == {"BLUE", "GREEN"}
    assert fratricide_victims(_ALLIED, "BLUE", {"RED": 9.0}) == {}
    assert fratricide_victims(_ALLIED, "BLUE", {"GREEN": 0.0}) == {}  # 0 損失不算受害


# ---- 預檢接線 ----


def _world_with(session_factory, relation: str, allow: bool):  # type: ignore[no-untyped-def]
    from _order_fakes import seed_world

    from app.models.tables import TacticalUnit, WargameSession

    world = seed_world(session_factory)
    with session_factory() as db:
        db.get(TacticalUnit, world.red_unit_id).faction = "GREEN"
        session = db.get(WargameSession, world.session_id)
        session.faction_relations = [["BLUE", "GREEN", relation]]
        session.allow_fratricide = allow or None
        db.commit()
    return world


def _engage(db, world):  # type: ignore[no-untyped-def]
    from _order_fakes import FakeGateway

    from app.factions.session_store import load_session_relations
    from app.orders.schemas import OrderRequest, OrderType
    from app.orders.service import OrderService

    return OrderService(
        db, FakeGateway(), relations=load_session_relations(db, world.session_id)
    ).submit(
        world.session_id,
        OrderRequest(
            unit_id=world.blue_unit_id,
            order_type=OrderType.ENGAGE,
            payload={"target_unit_id": world.red_unit_id},
        ),
        world.blue_issuer_id,
    )


def test_switch_off_still_rejects_an_ally(session_factory) -> None:  # type: ignore[no-untyped-def]
    from app.errors import PrecheckFailedError

    world = _world_with(session_factory, "ALLIED", allow=False)
    db = session_factory()
    with pytest.raises(PrecheckFailedError, match="非敵對"):
        _engage(db, world)
    db.close()


def test_switch_on_lets_the_order_through_with_a_warning(session_factory) -> None:  # type: ignore[no-untyped-def]
    """驗收條文：開啟時改為**強警告 + 須 override**，令照常執行。

    警語走 `PrecheckCheck(passed=True)`——不影響 feasible，但會出現在
    `PrecheckResult.checks` 讓前端顯示。靜靜放行等於沒做這張卡。
    """
    world = _world_with(session_factory, "ALLIED", allow=True)
    db = session_factory()
    resp = _engage(db, world)
    assert resp.status.value == "VALIDATED"
    warnings = [c for c in (resp.precheck.checks if resp.precheck else []) if "誤傷" in c.detail]
    assert warnings, "放行了卻沒有任何警語——那正是這張卡要避免的"
    assert all(c.passed for c in warnings)
    db.close()


def test_switch_on_still_rejects_a_neutral(session_factory) -> None:  # type: ignore[no-untyped-def]
    from app.errors import PrecheckFailedError

    world = _world_with(session_factory, "NEUTRAL", allow=True)
    db = session_factory()
    with pytest.raises(PrecheckFailedError, match="非敵對"):
        _engage(db, world)
    db.close()


def test_an_existing_session_with_a_null_flag_behaves_exactly_as_before(session_factory) -> None:  # type: ignore[no-untyped-def]
    """既有局的 `allowFratricide` 是 NULL。**中性預設**：一律當作關閉。

    欄位刻意 nullable 且無 default——NOT NULL + default 會回頭改掉每一個
    進行中的既有局的語義。
    """
    from _order_fakes import seed_world

    from app.models.tables import TacticalUnit, WargameSession
    from app.orders.precheck import _allow_fratricide

    world = seed_world(session_factory)
    db = session_factory()
    assert db.get(WargameSession, world.session_id).allow_fratricide is None
    assert _allow_fratricide(db, db.get(TacticalUnit, world.blue_unit_id)) is False
    db.close()


# ---- 想定層：九層都要到位 ----


def _example_pkg() -> str:
    """用**真的官方想定**當底，而不是手刻一份最小 bundle。

    scenario.schema.json 的必填欄位（mode 列舉、顏色格式、victory_conditions 非空、
    orbat 形狀…）加起來夠多，手刻一份只會測到我對 schema 的記憶力。
    """
    import pathlib as _p

    return str(
        _p.Path(__file__).resolve().parents[3] / "scenarios" / "examples" / "tutorial-platoon"
    )


def test_the_switch_survives_an_export_import_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """匯出 → 匯入，開關不能消失。

    `scenario_to_dict` 是**手寫白名單**（BL-2 記錄過那個原案：`request_quotas` 與
    `indirect_fire_requires_approval` 就是這樣靜靜掉的）。這條把新開關釘進那份清單。
    """
    import dataclasses

    from app.scenario import dump_scenario_package, load_scenario_package, scenario_to_dict

    loaded = dataclasses.replace(load_scenario_package(_example_pkg()), allow_fratricide=True)
    assert scenario_to_dict(loaded).get("allow_fratricide") is True
    dump_scenario_package(loaded, str(tmp_path))
    assert load_scenario_package(str(tmp_path)).allow_fratricide is True


def test_the_switch_is_omitted_when_off_so_existing_exports_stay_byte_identical() -> None:
    """關閉時**不輸出這個鍵**：無條件寫 `false` 會讓每一份既有想定的匯出檔都變了
    （`test_official_scenario_export_is_byte_identical` 會紅）。"""
    from app.scenario import load_scenario_package, scenario_to_dict

    dumped = scenario_to_dict(load_scenario_package(_example_pkg()))
    assert "allow_fratricide" not in dumped


def test_the_switch_reaches_the_db_column_when_a_session_opens() -> None:
    """loader → DB 那一跳**沒有任何既有測試覆蓋**（既有兩個開關的測試都直接寫 row）。

    想定宣告了、DB 卻沒收到 → 消費端永遠讀到 None → 開關等於不存在，而且全綠。
    """
    import inspect

    from app.scenario import loader

    src = inspect.getsource(loader.create_session_from_scenario)
    assert "allow_fratricide=loaded.allow_fratricide" in src, (
        "create_session_from_scenario 沒有把 allow_fratricide 寫進 WargameSession"
    )


def test_the_scenario_schema_accepts_the_new_key() -> None:
    """`scenario.schema.json` 的 root 是 `additionalProperties: false`——
    沒宣告的鍵會讓整包想定**載入失敗**，不是被忽略。"""
    import json
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3]
    schema = json.loads((root / "contracts" / "scenario.schema.json").read_text())
    assert "allow_fratricide" in schema["properties"]
    assert schema["properties"]["allow_fratricide"]["type"] == "boolean"


# ---- FRATRICIDE 事件：受眾是這一段最容易錯的地方 ----


def _ledger(event_type: str, initiator: str | None, target: str | None):  # type: ignore[no-untyped-def]
    from app.state.ledger import LedgerEvent

    return LedgerEvent(event_type=event_type, tick=1, initiator_id=initiator, target_id=target)


def test_a_summary_shaped_event_would_leak_to_everyone() -> None:
    """**這條記錄的是為什麼一個受害者一筆**。

    `event_audience` 由 `initiator_id`/`target_id` 的陣營推導受眾，兩者都取不到陣營時
    回 `None`＝**全域廣播**。把誤傷做成一筆總結事件（受害者塞進 ai_decision、
    `target_id` 留空）的話，敵軍會立刻知道對面在自相殘殺。
    """
    from app.state.broadcaster import event_audience

    faction_for = {"B1": "BLUE", "G1": "GREEN"}.get
    assert event_audience(_ledger("FRATRICIDE", None, None), faction_for) is None  # ← 全域


def test_one_event_per_victim_reaches_only_the_two_factions_involved() -> None:
    from app.state.broadcaster import event_audience

    faction_for = {"B1": "BLUE", "G1": "GREEN", "R1": "RED"}.get
    audience = event_audience(_ledger("FRATRICIDE", "B1", "G1"), faction_for)
    assert audience == ["BLUE", "GREEN"]
    assert "RED" not in (audience or [])


def test_the_ws_envelope_carries_the_payload_not_an_empty_shell() -> None:
    """WS 信封只從 `ai_decision` 抄一份**固定 allowlist**。沒列進去的鍵，
    事件到了 COP 只剩型別對、內容全空。"""
    from app.state.broadcaster import build_event_envelope
    from app.state.ledger import LedgerEvent

    event = LedgerEvent(
        event_type="FRATRICIDE",
        tick=1,
        initiator_id="B1",
        target_id="G1",
        damage_calc=4.5,
        ai_decision={"cause": "AREA_FIRE", "shooter_faction": "BLUE"},
    )
    envelope = build_event_envelope(event, None)
    payload = envelope["payload"]
    assert payload["cause"] == "AREA_FIRE"
    assert payload["shooter_faction"] == "BLUE"


def test_fratricide_is_an_aar_bookmark() -> None:
    """誤傷是檢討會最該停下來看的一格——不註冊就只是帳本裡的一行字。"""
    from app.aar.replay import BOOKMARK_TYPES

    assert "FRATRICIDE" in BOOKMARK_TYPES
