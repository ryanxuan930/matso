"""複製推演局時**不可以掉想定衍生欄**（Backlog 清倉）。

## 為什麼這條要用「掃 schema」而不是逐欄列舉

`clone_session` 原本漏掉七個欄位（`msel`/`roe`/`mobilityOverrides`/`noStrikeZones`/
`requestQuotas`/`indirectFireRequiresApproval`/`survivabilityMove`）。漏掉的後果不是
「少一點設定」，是**副本會沒有 MSEL、沒有 ROE、沒有禁射區地跑**——看起來一切正常，
直到你發現腳本事件永遠不觸發、被禁的武器可以隨便用。

逐欄列舉的測試會跟著程式一起漏：新增欄位時兩邊都忘記，測試照樣綠。
**改成掃 `WargameSession` 的欄位表**，任何新欄位都必須明確被歸類——
要嘛「該複製」（有測試驗），要嘛「刻意不複製」（列在豁免表並寫明理由）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.models.enums import SessionMode, UnitLevel
from app.models.tables import WargameSession

# **刻意不複製**的欄位，每個都要有理由。加新欄位到這裡前先想清楚。
_INTENTIONALLY_NOT_COPIED = {
    "id",  # 新局要新 id
    "name",  # 由請求指定（或加「（副本）」）
    "master_seed",  # **必須換**——同 seed 等於重跑同一局，不是新的一局
    "archived_at",  # 副本是活的
    "exercise_id",  # 演習歸屬由使用者重新決定（B1）
    "session_role",  # 同上
    "start_time",  # 副本是新的一局，時間戳由 DB 預設
    "end_time",  # 同上；來源局收場了不代表副本收場了
    # ⚠ 這裡曾經列了 `created_at` / `current_tick` / `params_sealed` 三個**不存在的欄位名**。
    # 豁免表寫錯字不會有人發現——那一欄就這樣靜靜掉出守門範圍。
    # 現在由 `test_the_exemption_list_only_names_real_columns` 釘住。
}


def _attribute_names() -> set[str]:
    """**用 `column_attrs` 不用 `columns`**：後者的 `.key` 是 DB 欄位名（camelCase），
    `getattr` 拿不到。屬性名才是 Python 這一側的真實鍵。"""
    return {a.key for a in WargameSession.__mapper__.column_attrs}


def _copyable_columns() -> set[str]:
    return _attribute_names() - _INTENTIONALLY_NOT_COPIED


def _seed_rich_session(db: Session) -> WargameSession:
    """一局**每個想定衍生欄都有值**的來源局。"""
    row = WargameSession(
        name="來源",
        master_seed=1,
        current_weather={"w": 1},
        mode=SessionMode.REALTIME,
        world_start_time=datetime(2026, 1, 1, 6, 0),
        scenario_id="scn-1",  # 副本要沿用同一份想定來源（AAR 要追得到出處）
        orbat_edit_factions=["BLUE"],
        faction_relations=[["BLUE", "GREEN", "ALLIED"]],
        msel=[{"id": "m1"}],
        roe={"BLUE": {"forbidden_weapons": ["X"]}},
        mobility_overrides={"FOOT": {"speed": 5}},
        no_strike_zones=[{"kind": "NO_STRIKE"}],
        request_quotas={"FIRE_SUPPORT": 3},
        indirect_fire_requires_approval=True,
        survivability_move={"missions_before_move": 2},
        allow_fratricide=True,
        day_night={"sunrise_min": 330, "sunset_min": 1080},
        aggregate_adjudication_level=UnitLevel.BRIGADE,
        victory_conditions=[{"faction": "BLUE", "condition": {"type": "all", "of": []}}],
        tick_rate_ms=30_000,
        faction_colors={"BLUE": "#0055ff"},
        faction_display_names={"BLUE": "第 21 旅"},
    )
    db.add(row)
    db.commit()
    return row


def test_the_source_session_actually_exercises_every_copyable_column(session_factory) -> None:  # type: ignore[no-untyped-def]
    """**這條在守下面那條的有效性。**

    下面的比對是 `getattr(clone, n) != getattr(src, n)`——來源局若沒給某一欄值，
    兩邊都是 None、比對相等、測試綠，而那一欄其實根本沒被複製。
    **這正是實際發生過的事**：`aggregate_adjudication_level` / `victory_conditions` /
    `tick_rate_ms` / `faction_colors` / `faction_display_names` 五欄先後被加進模型，
    每一次都沒進 `clone_session`，而這份測試從頭到尾都是綠的。

    所以先釘住「來源局的每一個該複製的欄位都有可辨識的值」。
    """
    db = session_factory()
    src = _seed_rich_session(db)
    blank = [n for n in sorted(_copyable_columns()) if getattr(src, n) in (None, "", [], {})]
    db.close()

    assert not blank, (
        f"`_seed_rich_session` 沒有給這些欄位值：{blank}。\n"
        "沒有值 → 下面那條比對會是假綠。加欄位到模型時，這裡也要給一個可辨識的值。"
    )


def test_the_exemption_list_only_names_real_columns() -> None:
    """豁免表寫錯字（或欄位被改名）會讓那一欄**靜靜掉出守門範圍**。

    ⚠ 原本這裡是 `attrs - (attrs - exempt) - exempt`——那個集合恆為空集，
    測試永遠綠、什麼都沒證明。恆真的守門比沒有守門更糟，因為它讓人以為有守。
    """
    unknown = _INTENTIONALLY_NOT_COPIED - _attribute_names()

    assert not unknown, f"豁免表裡這些名字不是 WargameSession 的欄位：{sorted(unknown)}"


def test_cloning_carries_every_scenario_derived_field(session_factory) -> None:  # type: ignore[no-untyped-def]
    """副本必須帶著 MSEL / ROE / 禁射區 / 配額 / 火協開關 / 陣地變換 / 誤傷 / 晝夜。"""
    from app.auth.schemas import CurrentUser
    from app.lobby.schemas import CloneSessionRequest
    from app.lobby.service import LobbyService
    from app.models.enums import UserRole
    from app.models.tables import User

    db = session_factory()
    src = _seed_rich_session(db)
    admin = User(username="boss", password_hash="x", role=UserRole.ADMIN)
    db.add(admin)
    db.commit()
    actor = CurrentUser(id=admin.id, username="boss", role=UserRole.ADMIN)

    summary = LobbyService(db).clone_session(actor, src.id, CloneSessionRequest(name="副本"))
    clone = db.get(WargameSession, summary.id)
    assert clone is not None

    missing = [
        name for name in sorted(_copyable_columns()) if getattr(clone, name) != getattr(src, name)
    ]
    assert not missing, f"複製時掉了這些欄位：{missing}"
    db.close()


def test_the_clone_gets_a_different_seed(session_factory: sessionmaker[Session]) -> None:
    """**必須換 seed**——同 seed 等於重跑同一局，不是新的一局。"""
    from app.auth.schemas import CurrentUser
    from app.lobby.schemas import CloneSessionRequest
    from app.lobby.service import LobbyService
    from app.models.enums import UserRole
    from app.models.tables import User

    db = session_factory()
    src = _seed_rich_session(db)
    admin = User(username="boss", password_hash="x", role=UserRole.ADMIN)
    db.add(admin)
    db.commit()
    actor = CurrentUser(id=admin.id, username="boss", role=UserRole.ADMIN)
    summary = LobbyService(db).clone_session(actor, src.id, CloneSessionRequest(name="副本"))
    assert db.get(WargameSession, summary.id).master_seed != src.master_seed
    db.close()


def test_copy_json_does_not_alias(session_factory) -> None:  # type: ignore[no-untyped-def]
    """直接指派會讓副本與原局**在 commit 之前**共用同一個 Python 物件，改一邊動兩邊。

    ⚠ 誠實記一筆：**端到端測不出這件事**。把 `_copy_json(src.msel)` 換成 `src.msel`
    之後，端到端測試照樣綠——因為讀回來的 `clone.msel` 是從 DB 反序列化的新物件，
    JSON round-trip 本身就把別名關係打斷了（突變測試證明的）。
    所以這一條直接測那個提供保證的函式，而不是假裝端到端驗得到。
    """
    from app.lobby.service import _copy_json

    source = {"a": [1, 2]}
    copied = _copy_json(source)
    assert copied == source and copied is not source

    listed = [1, 2]
    assert _copy_json(listed) == listed and _copy_json(listed) is not listed

    # 純量原樣回傳（沒有別名問題）。
    assert _copy_json(True) is True
    assert _copy_json(None) is None


# ---- 單位層：`branch` 就是從這個缺口掉出去的 ----

# **刻意不複製**的單位欄位，每個都要有理由。
_UNIT_NOT_COPIED = {
    "id",  # 新單位要新 id
    "session_id",  # 指向新局
    "parent_id",  # 第二階段以 old→new 映射重寫（直接複製會指到舊局的單位）
}


def _unit_attribute_names() -> set[str]:
    from app.models.tables import TacticalUnit

    return {a.key for a in TacticalUnit.__mapper__.column_attrs}


def test_the_unit_exemption_list_only_names_real_columns() -> None:
    assert not (_UNIT_NOT_COPIED - _unit_attribute_names())


def test_cloning_carries_every_unit_column(session_factory) -> None:  # type: ignore[no-untyped-def]
    """**`branch` 就是從這裡掉出去的。**

    session 層有 schema 掃描守門，單位層一條都沒有——於是 `branch`（後來加的欄位）
    沒進 `clone_session` 也沒人發現。

    後果不只是圖示變成通用框：`branch=ENGINEER` 決定破障/設障令下不下得了、
    雷區通過機率、障礙通過速度。複製一局，工兵連就失去全部工兵能力，
    而畫面上只是符號變了——很難聯想到「為什麼破不了障」。
    """
    from app.auth.schemas import CurrentUser
    from app.lobby.schemas import CloneSessionRequest
    from app.lobby.service import LobbyService
    from app.models.enums import CommsState, UnitBranch, UnitLevel, UserRole
    from app.models.tables import TacticalUnit, User

    db = session_factory()
    src = _seed_rich_session(db)
    unit = TacticalUnit(
        session_id=src.id,
        designation="工兵連",
        unit_level=UnitLevel.COMPANY,
        branch=UnitBranch.ENGINEER,  # 這一欄正是漏掉的那個
        faction="BLUE",
        is_fixed=True,
        attributes={"unit_kind": "ENGINEER"},
        current_lat=23.5,
        current_lng=121.0,
        elevation=120.0,
        authorized_strength=120.0,
        current_strength=95.0,
        personnel_authorized=120,
        personnel_current=95,
        health_status=79.0,
        comms_status=CommsState.DEGRADED,
    )
    db.add(unit)
    admin = User(username="boss2", password_hash="x", role=UserRole.ADMIN)
    db.add(admin)
    db.commit()

    # 守門的守門：來源單位每一個該複製的欄位都要有可辨識的值。
    copyable = _unit_attribute_names() - _UNIT_NOT_COPIED
    blank = [n for n in sorted(copyable) if getattr(unit, n) in (None, "", [], {})]
    assert not blank, f"來源單位沒有給這些欄位值，比對會是假綠：{blank}"

    actor = CurrentUser(id=admin.id, username="boss2", role=UserRole.ADMIN)
    summary = LobbyService(db).clone_session(actor, src.id, CloneSessionRequest(name="副本"))
    clone_unit = db.query(TacticalUnit).filter(TacticalUnit.session_id == summary.id).one()

    missing = [n for n in sorted(copyable) if getattr(clone_unit, n) != getattr(unit, n)]
    assert not missing, f"複製單位時掉了這些欄位：{missing}"
    db.close()
