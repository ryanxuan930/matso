"""撤收建檔與銷毀模式（WP-B1b）。

三件事：
1. 歸檔封包的內容與**帳本讀法**（原樣，不做 ADR 007 邏輯截斷）。
2. 銷毀的三道閘門（ADMIN／已 ARCHIVED／名稱逐字確認）。
3. **既有的資料殘留 bug**：`delete_session` 的手寫刪除清單漏了四張表。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import auth_header, login, make_client, seed_user
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.lobby.purge import purge_session_rows, session_scoped_models
from app.main import app
from app.models import (
    FirePlan,
    FirePlanTarget,
    Message,
    Request,
    TacticalEventLog,
    UserRole,
    WargameSession,
)
from app.models.enums import MessageKind, RequestKind


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _roles(factory: sessionmaker[Session]) -> TestClient:
    seed_user(factory, username="wc", role=UserRole.EXERCISE_DIRECTOR)
    seed_user(factory, username="root", role=UserRole.ADMIN)
    return make_client(factory)


def _h(client: TestClient, who: str) -> dict[str, str]:
    return auth_header(login(client, who, "pw123")["access_token"])


def _archived_exercise(client: TestClient, wc: dict[str, str], name: str = "撤收演習") -> str:
    """建演習、掛一局、一路推到 ARCHIVED。"""
    ex = client.post("/api/v1/exercises", json={"name": name}, headers=wc).json()
    sid = client.post("/api/v1/sessions", json={"name": "資料局"}, headers=wc).json()["id"]
    client.post(
        f"/api/v1/exercises/{ex['id']}/sessions",
        json={"session_id": sid, "session_role": "MAIN"},
        headers=wc,
    )
    for phase in ("REHEARSAL", "EXECUTION", "REVIEW", "ARCHIVED"):
        view = client.get(f"/api/v1/exercises/{ex['id']}", headers=wc).json()
        for item in view["checklist"]:
            if item["phase"] == view["phase"] and item["required"] and not item["done"]:
                client.patch(
                    f"/api/v1/exercises/{ex['id']}/checklist/{item['key']}",
                    json={"done": True},
                    headers=wc,
                )
        r = client.patch(f"/api/v1/exercises/{ex['id']}/phase", json={"phase": phase}, headers=wc)
        assert r.status_code == 200, r.text
    return str(ex["id"])


# ---- 歸檔封包 ----


def test_bundle_contains_the_whole_exercise(session_factory: sessionmaker[Session]) -> None:
    client = _roles(session_factory)
    wc = _h(client, "wc")
    ex_id = _archived_exercise(client, wc)
    bundle = client.get(f"/api/v1/exercises/{ex_id}/bundle", headers=wc).json()

    assert bundle["bundle_version"] == "1.0"
    assert bundle["exercise"]["phase"] == "ARCHIVED"
    assert len(bundle["sessions"]) == 1
    assert bundle["content_hash"]
    # 稽核軌跡也在封包裡：歸檔的意義之一就是「這場演習是怎麼走過來的」。
    assert [a["action"] for a in bundle["audit"]][:1] == ["EXERCISE_CREATED"]
    # 帳本區塊帶鏈驗結果——事後有人問「這份資料可信嗎」，答案要在封包裡。
    ledger = bundle["sessions"][0]["ledger"]
    assert ledger["verified"] is True and ledger["count"] == 0


def test_exporting_the_bundle_leaves_a_trace(session_factory: sessionmaker[Session]) -> None:
    """「誰在什麼時候把整場演習的完整資料帶走了」是資安要問的第一個問題。"""
    client = _roles(session_factory)
    wc = _h(client, "wc")
    ex_id = _archived_exercise(client, wc)
    bundle = client.get(f"/api/v1/exercises/{ex_id}/bundle", headers=wc).json()
    audit = client.get(f"/api/v1/exercises/{ex_id}/audit", headers=wc).json()
    exported = next(a for a in audit if a["action"] == "BUNDLE_EXPORTED")
    assert exported["detail"]["content_hash"] == bundle["content_hash"]


def test_bundle_is_not_for_operators(session_factory: sessionmaker[Session]) -> None:
    """封包是 ground truth（不套陣營投影）——只給全知。"""
    client = _roles(session_factory)
    seed_user(session_factory, username="bob", role=UserRole.COMMANDER)
    wc = _h(client, "wc")
    ex_id = _archived_exercise(client, wc)
    bob = _h(client, "bob")
    assert client.get(f"/api/v1/exercises/{ex_id}/bundle", headers=bob).status_code == 404


def test_bundle_hash_is_stable_across_calls(session_factory: sessionmaker[Session]) -> None:
    """雜湊要能當「歸檔後有沒有被動過」的比對基準，那它先得對同一份資料穩定。"""
    client = _roles(session_factory)
    wc = _h(client, "wc")
    ex_id = _archived_exercise(client, wc)
    a = client.get(f"/api/v1/exercises/{ex_id}/bundle", headers=wc).json()["content_hash"]
    b = client.get(f"/api/v1/exercises/{ex_id}/bundle", headers=wc).json()["content_hash"]
    assert a == b


# ---- 銷毀模式的三道閘門 ----


def test_destroy_requires_admin_not_merely_white_cell(
    session_factory: sessionmaker[Session],
) -> None:
    """`is_omniscient` 包含每一位白軍幕僚——用它等於把不可逆的銷毀開放給整個統裁組。"""
    client = _roles(session_factory)
    wc = _h(client, "wc")
    ex_id = _archived_exercise(client, wc)
    r = client.post(
        f"/api/v1/exercises/{ex_id}/destroy", json={"confirm_name": "撤收演習"}, headers=wc
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "AUTH_FORBIDDEN"


def test_destroy_requires_archived_phase(session_factory: sessionmaker[Session]) -> None:
    client = _roles(session_factory)
    wc = _h(client, "wc")
    ex = client.post("/api/v1/exercises", json={"name": "還在跑"}, headers=wc).json()
    r = client.post(
        f"/api/v1/exercises/{ex['id']}/destroy",
        json={"confirm_name": "還在跑"},
        headers=_h(client, "root"),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "EXERCISE_PHASE_INVALID"


def test_destroy_requires_the_exact_name(session_factory: sessionmaker[Session]) -> None:
    """二次確認若只是「再按一次是」，那不是確認、是多按一次。"""
    client = _roles(session_factory)
    wc = _h(client, "wc")
    ex_id = _archived_exercise(client, wc)
    r = client.post(
        f"/api/v1/exercises/{ex_id}/destroy",
        json={"confirm_name": "撤收演習 "},  # 尾隨空白
        headers=_h(client, "root"),
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "EXERCISE_DESTROY_UNCONFIRMED"


def test_destroy_removes_sessions_but_keeps_the_exercise_record(
    session_factory: sessionmaker[Session],
) -> None:
    """銷毀的是推演資料——「這場演習存在過、被誰在何時銷毀」正是稽核要保留的。"""
    client = _roles(session_factory)
    wc = _h(client, "wc")
    ex_id = _archived_exercise(client, wc)
    r = client.post(
        f"/api/v1/exercises/{ex_id}/destroy",
        json={"confirm_name": "撤收演習"},
        headers=_h(client, "root"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["sessions_destroyed"] == 1

    view = client.get(f"/api/v1/exercises/{ex_id}", headers=wc).json()
    assert view["sessions"] == []  # 局沒了
    audit = client.get(f"/api/v1/exercises/{ex_id}/audit", headers=wc).json()
    assert audit[-1]["action"] == "DATA_DESTROYED"  # 演習與軌跡還在


# ---- 既有的資料殘留 bug ----


def test_purge_covers_every_session_scoped_table(
    session_factory: sessionmaker[Session],
) -> None:
    """**這條釘的是本卡修掉的既有 bug**。

    `delete_session` 的刪除清單是手寫的，而它已經漏了 `Message`/`Request`/`FirePlan`
    （這三張在 prisma 裡沒有 FK，所以不會噴錯——列就這樣永遠孤兒化）。
    改成由模型自省導出後，未來新增的 session 範圍表會自動入列。
    """
    names = {m.__name__ for m in session_scoped_models()}
    assert {"Message", "Request", "FirePlan"} <= names, "手寫清單漏掉的那三張"
    assert "TacticalUnit" in names
    # TacticalUnit 必須排在最後：Order/IntelContact 參照它。
    assert [m.__name__ for m in session_scoped_models()][-1] == "TacticalUnit"


def test_purge_actually_deletes_the_previously_orphaned_rows(
    session_factory: sessionmaker[Session],
) -> None:
    """自省清單只是機制——這條驗真的刪掉了。"""
    db = session_factory()
    session = WargameSession(name="有殘留的局", master_seed=1, current_weather={})
    db.add(session)
    db.flush()
    sid = session.id
    db.add(
        Message(
            session_id=sid,
            kind=MessageKind.FREE_TEXT,
            from_user_id="u1",
            to_faction="BLUE",
            body="hi",
            tick=1,
        )
    )
    db.add(
        Request(
            session_id=sid,
            faction="BLUE",
            kind=RequestKind.FIRE_SUPPORT,
            requested_by_id="u1",
            requested_at_tick=1,
            params={},
        )
    )
    plan = FirePlan(session_id=sid, faction="BLUE", name="計畫", created_at_tick=0)
    db.add(plan)
    db.flush()
    db.add(
        FirePlanTarget(
            plan_id=plan.id, seq=0, target_lat=24.0, target_lng=121.0, shooter_unit_id="x"
        )
    )
    db.add(
        TacticalEventLog(
            session_id=sid,
            seq=0,
            tick=0,
            event_type="TEST",
            weather_snapshot={},
            terrain_modifier=1.0,
            ai_decision={},
            prev_hash="0" * 64,
            self_hash="1" * 64,
        )
    )
    db.commit()

    purge_session_rows(db, sid)
    db.commit()

    for model, where in (
        (Message, Message.session_id == sid),
        (Request, Request.session_id == sid),
        (FirePlan, FirePlan.session_id == sid),
        (FirePlanTarget, FirePlanTarget.plan_id == plan.id),
        (TacticalEventLog, TacticalEventLog.session_id == sid),
    ):
        left = db.execute(select(model).where(where)).scalars().all()
        assert left == [], f"{model.__name__} 有殘留"
    assert db.get(WargameSession, sid) is None
    db.close()
