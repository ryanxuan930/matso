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

from sqlalchemy.orm import Session, sessionmaker

from app.models.enums import SessionMode
from app.models.tables import WargameSession

# **刻意不複製**的欄位，每個都要有理由。加新欄位到這裡前先想清楚。
_INTENTIONALLY_NOT_COPIED = {
    "id",  # 新局要新 id
    "name",  # 由請求指定（或加「（副本）」）
    "master_seed",  # **必須換**——同 seed 等於重跑同一局，不是新的一局
    "created_at",
    "archived_at",  # 副本是活的
    "exercise_id",  # 演習歸屬由使用者重新決定（B1）
    "session_role",  # 同上
    "params_sealed",  # 簽證不隨副本走（B4：簽證是對「那一局」的）
    "current_tick",  # 副本從頭開始
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
        world_start_time=None,
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
    )
    db.add(row)
    db.commit()
    return row


def test_every_column_is_either_copied_or_explicitly_exempt(session_factory) -> None:  # type: ignore[no-untyped-def]
    """**這條是防呆**：新增 `WargameSession` 欄位時，要嘛下面那條測試會抓到沒複製，
    要嘛你得明確把它加進豁免表並寫理由。沒有第三條路。"""
    unclassified = _attribute_names() - _copyable_columns() - _INTENTIONALLY_NOT_COPIED
    assert not unclassified, f"這些欄位沒有被歸類：{sorted(unclassified)}"


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
