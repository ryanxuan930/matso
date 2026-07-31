"""帳本查詢端點（UI-P3）——事後爭議裁決的原始證據。

這條路徑**在契約裡躺了很久而後端一直是 404**。契約說謊比缺功能難查：
前端拿得到型別、按下去吃 404，而所有閘門都是綠的
（`test_contract_conformance` 的已知漂移清單把它列為豁免，這張卡把它刪掉了）。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import TEST_SETTINGS
from _order_fakes import order_token, seed_world
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db, get_settings
from app.main import app
from app.models import TacticalEventLog, User
from app.models.enums import UserRole


@pytest.fixture(autouse=True)
def _clear() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _client(factory: sessionmaker[Session]) -> TestClient:
    def _db() -> Iterator[Session]:
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS
    return TestClient(app)


def _hdr(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _add_events(
    factory: sessionmaker[Session], session_id: str, specs: list[tuple[int, str]], **kw: object
) -> None:
    """種事件。`specs` 是 (seq, event_type)。"""
    with factory() as db:
        for seq, etype in specs:
            db.add(
                TacticalEventLog(
                    session_id=session_id,
                    seq=seq,
                    tick=seq,
                    event_type=etype,
                    weather_snapshot={},
                    terrain_modifier=1.0,
                    ai_decision={},
                    # hash chain 欄位是 NOT NULL。這裡種的是**測試資料**，
                    # 不走 `LedgerWriter`（那會把 seq 也自己算掉，就控制不了分頁情境）。
                    prev_hash="",
                    self_hash=f"h{seq}",
                    **kw,  # type: ignore[arg-type]
                )
            )
        db.commit()


def test_the_endpoint_exists_at_all(session_factory: sessionmaker[Session]) -> None:
    """**這條就是這張卡的全部理由**：契約宣告了很久，後端一直回 404。"""
    world = seed_world(session_factory)
    _add_events(session_factory, world.session_id, [(1, "MOVE_COMPLETED")])
    client = _client(session_factory)
    tok = order_token(world.cmdr_user_id, UserRole.COMMANDER)

    r = client.get(f"/api/v1/sessions/{world.session_id}/ledger", headers=_hdr(tok))
    assert r.status_code == 200, r.text
    assert [e["event_type"] for e in r.json()["events"]] == ["MOVE_COMPLETED"]


def test_paging_walks_the_whole_ledger_without_repeating_or_skipping(
    session_factory: sessionmaker[Session],
) -> None:
    """游標分頁要**不重不漏**。帳本是 append-only，seq 單調，所以游標就是 seq。"""
    world = seed_world(session_factory)
    _add_events(session_factory, world.session_id, [(i, "MOVE_COMPLETED") for i in range(1, 12)])
    client = _client(session_factory)
    tok = order_token(world.cmdr_user_id, UserRole.COMMANDER)

    seen: list[int] = []
    after = 0
    for _ in range(10):  # 上限保護：真的分不完就讓測試失敗而不是無窮迴圈
        r = client.get(
            f"/api/v1/sessions/{world.session_id}/ledger",
            params={"after_seq": after, "limit": 4},
            headers=_hdr(tok),
        )
        page = r.json()
        seen.extend(e["seq"] for e in page["events"])
        if not page["has_more"]:
            break
        after = page["next_after_seq"]

    assert seen == list(range(1, 12)), seen


def test_the_type_filter_is_applied(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    _add_events(
        session_factory,
        world.session_id,
        [(1, "MOVE_COMPLETED"), (2, "ENGAGEMENT_RESOLVED"), (3, "MOVE_COMPLETED")],
    )
    client = _client(session_factory)
    tok = order_token(world.cmdr_user_id, UserRole.COMMANDER)

    r = client.get(
        f"/api/v1/sessions/{world.session_id}/ledger",
        params={"types": "ENGAGEMENT_RESOLVED"},
        headers=_hdr(tok),
    )
    assert [e["seq"] for e in r.json()["events"]] == [2]


def test_the_cursor_survives_a_page_that_fog_empties_out(
    session_factory: sessionmaker[Session],
) -> None:
    """**游標要取自投影前的最後一筆**。

    迷霧可能把一整頁的事件全部剔掉（那一頁都是敵軍的事）。若游標取投影**後**的最後一筆，
    空頁就沒有游標可回、或回上一頁的舊值——分頁會卡在原地永遠翻不完，
    而畫面上看起來只是「載入中」。
    """
    world = seed_world(session_factory)
    # 全部掛在紅軍單位上——藍軍指揮官一筆都看不到。
    _add_events(
        session_factory,
        world.session_id,
        [(i, "ENGAGEMENT_RESOLVED") for i in range(1, 4)],
        initiator_id=world.red_unit_id,
        target_id=world.red_unit_id,
    )
    _add_events(
        session_factory,
        world.session_id,
        [(4, "MOVE_COMPLETED")],
        initiator_id=world.blue_unit_id,
    )
    client = _client(session_factory)
    tok = order_token(world.cmdr_user_id, UserRole.COMMANDER)

    first = client.get(
        f"/api/v1/sessions/{world.session_id}/ledger",
        params={"after_seq": 0, "limit": 3},
        headers=_hdr(tok),
    ).json()
    assert first["events"] == [], "藍軍不該看得到純紅軍的交戰"
    assert first["has_more"] is True
    assert first["next_after_seq"] == 3, "游標被迷霧吃掉了——下一頁會從錯的位置開始"

    second = client.get(
        f"/api/v1/sessions/{world.session_id}/ledger",
        params={"after_seq": first["next_after_seq"], "limit": 3},
        headers=_hdr(tok),
    ).json()
    assert [e["seq"] for e in second["events"]] == [4]


def test_a_non_participant_is_refused(session_factory: sessionmaker[Session]) -> None:
    """存取規則與 AAR 完全相同——不另寫一套（那正是 fog 漏洞的成因）。"""
    world = seed_world(session_factory)
    with session_factory() as db:
        outsider = User(username="nobody", password_hash="x", role=UserRole.COMMANDER)
        db.add(outsider)
        db.commit()
        oid = outsider.id
    client = _client(session_factory)

    r = client.get(
        f"/api/v1/sessions/{world.session_id}/ledger",
        headers=_hdr(order_token(oid, UserRole.COMMANDER)),
    )
    assert r.status_code == 403
