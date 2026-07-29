"""演習專案 REST（WP-B1）：階段機、整備勾稽、掛/卸 session、稽核軌跡、權限。

驗收條文（SPEC_V2 §6 WP-B1）「建演習→掛 2 個預推局＋1 正式局→階段推進留痕→…；
**獨立 session 流程完全不受影響**」在此逐條釘住。獨立局那條在 `test_lobby_api.py`
的既有測試 + 本檔的 `test_standalone_session_is_untouched`。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import auth_header, login, make_client, seed_user
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.main import app
from app.models import UserRole


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _white(factory: sessionmaker[Session]) -> tuple[TestClient, dict[str, str]]:
    seed_user(factory, username="wc", role=UserRole.EXERCISE_DIRECTOR)
    client = make_client(factory)
    tokens = login(client, "wc", "pw123")
    return client, auth_header(tokens["access_token"])


def _make_session(client: TestClient, h: dict[str, str], name: str) -> str:
    r = client.post("/api/v1/sessions", json={"name": name}, headers=h)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def _create(client: TestClient, h: dict[str, str], name: str = "光復演習") -> dict:  # type: ignore[type-arg]
    r = client.post("/api/v1/exercises", json={"name": name}, headers=h)
    assert r.status_code == 201, r.text
    return dict(r.json())


def _tick_all(client: TestClient, h: dict[str, str], ex_id: str, phase: str) -> None:
    """把某階段的必要項目全部勾完（測階段機時不想每次手寫一串勾稽）。"""
    ex = client.get(f"/api/v1/exercises/{ex_id}", headers=h).json()
    for item in ex["checklist"]:
        if item["phase"] == phase and item["required"]:
            r = client.patch(
                f"/api/v1/exercises/{ex_id}/checklist/{item['key']}",
                json={"done": True},
                headers=h,
            )
            assert r.status_code == 200, r.text


# ---- 建立與預設狀態 ----


def test_new_exercise_starts_in_prep_with_default_checklist(
    session_factory: sessionmaker[Session],
) -> None:
    client, h = _white(session_factory)
    ex = _create(client, h)
    assert ex["phase"] == "PREP"
    assert ex["sessions"] == []
    keys = {i["key"] for i in ex["checklist"]}
    # 整備會議 ×3 是 SPEC 明列的勾稽項；params_sealed 是 WP-B4 的掛點。
    assert {"prep_meeting_1", "prep_meeting_2", "prep_meeting_3", "params_sealed"} <= keys
    assert all(i["done"] is False for i in ex["checklist"])


# ---- 階段機 ----


def test_phase_cannot_skip_ahead(session_factory: sessionmaker[Session]) -> None:
    client, h = _white(session_factory)
    ex = _create(client, h)
    r = client.patch(f"/api/v1/exercises/{ex['id']}/phase", json={"phase": "EXECUTION"}, headers=h)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "EXERCISE_PHASE_INVALID"


def test_phase_cannot_go_backwards(session_factory: sessionmaker[Session]) -> None:
    """倒退不開放：WP-B4 的參數簽證與稽核軌跡的意義都來自單調（見 phases.py）。"""
    client, h = _white(session_factory)
    ex = _create(client, h)
    _tick_all(client, h, ex["id"], "PREP")
    assert (
        client.patch(
            f"/api/v1/exercises/{ex['id']}/phase", json={"phase": "REHEARSAL"}, headers=h
        ).status_code
        == 200
    )
    r = client.patch(f"/api/v1/exercises/{ex['id']}/phase", json={"phase": "PREP"}, headers=h)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "EXERCISE_PHASE_INVALID"


def test_incomplete_checklist_blocks_the_advance(session_factory: sessionmaker[Session]) -> None:
    """**這條是 checklist 存在的理由**：只當提示的話，與沒有這個機制無異。"""
    client, h = _white(session_factory)
    ex = _create(client, h)
    r = client.patch(f"/api/v1/exercises/{ex['id']}/phase", json={"phase": "REHEARSAL"}, headers=h)
    assert r.status_code == 403
    body = r.json()["error"]
    assert body["code"] == "EXERCISE_CHECKLIST_INCOMPLETE"
    # 逐鍵列出來，不然操作員只知道「有東西沒做」卻不知道是哪一項。
    assert set(body["details"]["missing"]) == {
        "prep_meeting_1",
        "prep_meeting_2",
        "prep_meeting_3",
        "scenario_published",
    }


def test_optional_items_do_not_block(session_factory: sessionmaker[Session]) -> None:
    """`saturation_test` 是 required=False——沒勾也推得動。"""
    client, h = _white(session_factory)
    ex = _create(client, h)
    _tick_all(client, h, ex["id"], "PREP")
    after = client.patch(
        f"/api/v1/exercises/{ex['id']}/phase", json={"phase": "REHEARSAL"}, headers=h
    )
    assert after.status_code == 200
    sat = next(i for i in after.json()["checklist"] if i["key"] == "saturation_test")
    assert sat["done"] is False  # 確認真的沒被勾（否則這條測試在測別的東西）


def test_checklist_tick_records_who_and_when(session_factory: sessionmaker[Session]) -> None:
    client, h = _white(session_factory)
    ex = _create(client, h)
    after = client.patch(
        f"/api/v1/exercises/{ex['id']}/checklist/prep_meeting_1",
        json={"done": True},
        headers=h,
    ).json()
    item = next(i for i in after["checklist"] if i["key"] == "prep_meeting_1")
    assert item["done"] is True
    assert item["done_by"] and item["done_at"]
    # 取消勾稽要把痕跡也清掉——留著會讓「誰勾的」指向一個已經被撤銷的動作。
    again = client.patch(
        f"/api/v1/exercises/{ex['id']}/checklist/prep_meeting_1",
        json={"done": False},
        headers=h,
    ).json()
    item = next(i for i in again["checklist"] if i["key"] == "prep_meeting_1")
    assert (item["done"], item["done_by"], item["done_at"]) == (False, None, None)


def test_unknown_checklist_key_is_404(session_factory: sessionmaker[Session]) -> None:
    client, h = _white(session_factory)
    ex = _create(client, h)
    r = client.patch(f"/api/v1/exercises/{ex['id']}/checklist/nope", json={"done": True}, headers=h)
    assert r.status_code == 404


# ---- 掛/卸 session ----


def test_attach_two_rehearsals_and_one_main(session_factory: sessionmaker[Session]) -> None:
    """驗收條文的主線：一個演習掛 2 個預推局 + 1 個正式局。"""
    client, h = _white(session_factory)
    ex = _create(client, h)
    ids = {
        "REHEARSAL": [_make_session(client, h, "預推一"), _make_session(client, h, "預推二")],
        "MAIN": [_make_session(client, h, "正式局")],
    }
    for role, sids in ids.items():
        for sid in sids:
            r = client.post(
                f"/api/v1/exercises/{ex['id']}/sessions",
                json={"session_id": sid, "session_role": role},
                headers=h,
            )
            assert r.status_code == 200, r.text
    view = client.get(f"/api/v1/exercises/{ex['id']}", headers=h).json()
    assert len(view["sessions"]) == 3
    assert sorted(s["session_role"] for s in view["sessions"]) == [
        "MAIN",
        "REHEARSAL",
        "REHEARSAL",
    ]
    # 每局自己的摘要也要看得到歸屬（作戰方看得到自己這局屬於哪場演習）。
    summaries = {s["id"]: s for s in client.get("/api/v1/sessions", headers=h).json()}
    assert summaries[ids["MAIN"][0]]["exercise_id"] == ex["id"]
    assert summaries[ids["MAIN"][0]]["session_role"] == "MAIN"


def test_session_cannot_belong_to_two_exercises(session_factory: sessionmaker[Session]) -> None:
    client, h = _white(session_factory)
    a, b = _create(client, h, "甲演習"), _create(client, h, "乙演習")
    sid = _make_session(client, h, "共用局")
    client.post(f"/api/v1/exercises/{a['id']}/sessions", json={"session_id": sid}, headers=h)
    r = client.post(f"/api/v1/exercises/{b['id']}/sessions", json={"session_id": sid}, headers=h)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "EXERCISE_SESSION_CONFLICT"


def test_detach_returns_the_session_to_standalone(
    session_factory: sessionmaker[Session],
) -> None:
    client, h = _white(session_factory)
    ex = _create(client, h)
    sid = _make_session(client, h, "掛了又卸")
    client.post(
        f"/api/v1/exercises/{ex['id']}/sessions",
        json={"session_id": sid, "session_role": "MAIN"},
        headers=h,
    )
    after = client.delete(f"/api/v1/exercises/{ex['id']}/sessions/{sid}", headers=h)
    assert after.status_code == 200
    assert after.json()["sessions"] == []
    summary = next(s for s in client.get("/api/v1/sessions", headers=h).json() if s["id"] == sid)
    assert summary["exercise_id"] is None and summary["session_role"] is None


def test_deleting_the_exercise_does_not_delete_its_sessions(
    session_factory: sessionmaker[Session],
) -> None:
    """**刪專案不是銷毀資料**——掛在底下的局只是變回獨立局。

    把兩者綁在一起，「我按錯了想刪掉這個空專案」就會變成刪掉整場演習的資料。
    """
    client, h = _white(session_factory)
    ex = _create(client, h)
    sid = _make_session(client, h, "還活著")
    client.post(f"/api/v1/exercises/{ex['id']}/sessions", json={"session_id": sid}, headers=h)
    assert client.delete(f"/api/v1/exercises/{ex['id']}", headers=h).status_code == 204
    assert client.get(f"/api/v1/exercises/{ex['id']}", headers=h).status_code == 404
    summary = next(s for s in client.get("/api/v1/sessions", headers=h).json() if s["id"] == sid)
    assert summary["exercise_id"] is None


# ---- 稽核軌跡 ----


def test_every_mutation_leaves_a_trace(session_factory: sessionmaker[Session]) -> None:
    """階段推進留痕是驗收條文。**刻意不寫 TacticalEventLog**——那是 golden 驗的雜湊鏈。"""
    client, h = _white(session_factory)
    ex = _create(client, h)
    sid = _make_session(client, h, "一局")
    client.post(f"/api/v1/exercises/{ex['id']}/sessions", json={"session_id": sid}, headers=h)
    _tick_all(client, h, ex["id"], "PREP")
    client.patch(
        f"/api/v1/exercises/{ex['id']}/phase",
        json={"phase": "REHEARSAL", "note": "整備完成"},
        headers=h,
    )
    audit = client.get(f"/api/v1/exercises/{ex['id']}/audit", headers=h).json()
    actions = [a["action"] for a in audit]
    assert actions[0] == "EXERCISE_CREATED"
    assert "SESSION_ATTACHED" in actions
    assert actions.count("CHECKLIST_TICKED") == 4
    advanced = next(a for a in audit if a["action"] == "PHASE_ADVANCED")
    assert (advanced["from_phase"], advanced["to_phase"]) == ("PREP", "REHEARSAL")
    assert advanced["detail"]["note"] == "整備完成"


def test_audit_of_unknown_exercise_is_404_not_empty(
    session_factory: sessionmaker[Session],
) -> None:
    """回空 list 會讓呼叫端以為「這個演習存在，只是還沒有動作」。"""
    client, h = _white(session_factory)
    assert client.get("/api/v1/exercises/nope/audit", headers=h).status_code == 404


# ---- 權限 ----


def test_commander_cannot_see_exercises(session_factory: sessionmaker[Session]) -> None:
    """演習層是導演工具。作戰方看得到的是自己那一局（`SessionSummary.exercise_id`）。"""
    seed_user(session_factory, username="wc", role=UserRole.EXERCISE_DIRECTOR)
    seed_user(session_factory, username="bob", role=UserRole.COMMANDER)
    client = make_client(session_factory)
    wc_h = auth_header(login(client, "wc", "pw123")["access_token"])
    ex = _create(client, wc_h)
    bob_h = auth_header(login(client, "bob", "pw123")["access_token"])
    # 404 而非 403：403 會回答「這個 id 存在」，那是列舉的入口。
    assert client.get("/api/v1/exercises", headers=bob_h).status_code == 404
    assert client.get(f"/api/v1/exercises/{ex['id']}", headers=bob_h).status_code == 404


def test_admin_can_look_but_not_advance(session_factory: sessionmaker[Session]) -> None:
    """ADMIN 是系統管理不是統裁（`faction_filter` 的既有裁示）——看得到、推不動。"""
    seed_user(session_factory, username="wc", role=UserRole.EXERCISE_DIRECTOR)
    seed_user(session_factory, username="root", role=UserRole.ADMIN)
    client = make_client(session_factory)
    wc_h = auth_header(login(client, "wc", "pw123")["access_token"])
    ex = _create(client, wc_h)
    _tick_all(client, wc_h, ex["id"], "PREP")

    admin_h = auth_header(login(client, "root", "pw123")["access_token"])
    assert client.get(f"/api/v1/exercises/{ex['id']}", headers=admin_h).status_code == 200
    r = client.patch(
        f"/api/v1/exercises/{ex['id']}/phase", json={"phase": "REHEARSAL"}, headers=admin_h
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "AUTH_FORBIDDEN"


def test_requires_auth(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory, username="wc", role=UserRole.EXERCISE_DIRECTOR)
    client = make_client(session_factory)
    assert client.get("/api/v1/exercises").status_code == 401
    assert client.post("/api/v1/exercises", json={"name": "x"}).status_code == 401


# ---- 獨立局零行為變更 ----


def test_standalone_session_is_untouched(session_factory: sessionmaker[Session]) -> None:
    """**本卡的驗收核心**：沒掛演習的局，兩個新欄一律 None，其餘欄位一如既往。"""
    client, h = _white(session_factory)
    _create(client, h)  # 系統裡有演習存在，但這一局沒掛上去
    sid = _make_session(client, h, "散局")
    summary = next(s for s in client.get("/api/v1/sessions", headers=h).json() if s["id"] == sid)
    assert summary["exercise_id"] is None
    assert summary["session_role"] is None
    assert summary["status"] == "ACTIVE"
    # 封存/還原/刪除照舊（#31 的既有流程不因為多了演習層而改變）。
    assert client.post(f"/api/v1/sessions/{sid}/archive", headers=h).json()["status"] == "ARCHIVED"
    assert client.post(f"/api/v1/sessions/{sid}/unarchive", headers=h).json()["status"] == "ACTIVE"
    assert client.delete(f"/api/v1/sessions/{sid}", headers=h).status_code == 204
