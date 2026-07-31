"""AAR 端點存取控制（O8，SPEC §14/§12）——參與者/ANALYST/全知可，其餘 403。"""

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


def _mk_user(factory: sessionmaker[Session], role: UserRole) -> str:
    with factory() as db:
        u = User(username=f"u-{role.value}", password_hash="x", role=role)
        db.add(u)
        db.commit()
        return u.id


def test_participant_can_access_aar(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    tok = order_token(world.cmdr_user_id, UserRole.COMMANDER)
    for path in ("aar/replay", "aar/stats", "aar/report"):
        r = client.get(f"/api/v1/sessions/{world.session_id}/{path}", headers=_hdr(tok))
        assert r.status_code == 200, path


def test_analyst_can_access(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    analyst = _mk_user(session_factory, UserRole.ANALYST)  # 非參與者，但 ANALYST 可看 AAR
    client = _client(session_factory)
    r = client.get(
        f"/api/v1/sessions/{world.session_id}/aar/stats",
        headers=_hdr(order_token(analyst, UserRole.ANALYST)),
    )
    assert r.status_code == 200


def test_non_participant_non_analyst_forbidden(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    outsider = _mk_user(session_factory, UserRole.OBSERVER)
    client = _client(session_factory)
    r = client.get(
        f"/api/v1/sessions/{world.session_id}/aar/replay",
        headers=_hdr(order_token(outsider, UserRole.OBSERVER)),
    )
    assert r.status_code == 403


def test_export_anonymize(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    client = _client(session_factory)
    tok = order_token(world.cmdr_user_id, UserRole.COMMANDER)
    r = client.get(
        f"/api/v1/sessions/{world.session_id}/aar/export",
        params={"fmt": "csv", "anonymize": "true"},
        headers=_hdr(tok),
    )
    assert r.status_code == 200 and "seq,tick,event_type" in r.text


def test_stats_response_matches_the_contract(session_factory: sessionmaker[Session]) -> None:
    """**回應體要照契約，而且要有東西驗它。**

    契約把 `attempts` / `engagements_fired` / `hits` / `stats_version` 都列為 required，
    但既有測試只斷言 `status_code == 200`，而 `test_contract_conformance` 比對的是
    **路徑集合**、完全不看 response schema。於是把那幾個鍵從 `api/aar.py` 的回傳
    dict 刪掉，82 條 aar/contract/archive 測試全綠——前端把它們宣告成必有，
    畫面會印「未射出 NaN 次」，而且沒有任何閘門會發現。

    這裡直接拿 `contracts/core_api.yaml` 的 `AarStats` 驗真的回應，
    契約與實作漂開就會紅。
    """
    import json
    from pathlib import Path

    import jsonschema
    import yaml

    spec = yaml.safe_load(
        (Path(__file__).resolve().parents[3] / "contracts" / "core_api.yaml").read_text("utf-8")
    )
    schema = spec["components"]["schemas"]["AarStats"]
    schema["components"] = spec["components"]  # 供 $ref 解析（本 schema 目前無 $ref，先備著）

    world = seed_world(session_factory)
    client = _client(session_factory)
    tok = order_token(world.cmdr_user_id, UserRole.COMMANDER)
    r = client.get(f"/api/v1/sessions/{world.session_id}/aar/stats", headers=_hdr(tok))
    assert r.status_code == 200

    body = r.json()
    jsonschema.validate(body, schema)
    missing = set(schema.get("required", [])) - set(body)
    assert not missing, f"回應少了契約宣告為必填的欄位：{sorted(missing)}；實得 {sorted(body)}"
    assert json.dumps(body)  # 可序列化（封存包要用）


def test_replay_changes_never_carry_null_keys(session_factory: sessionmaker[Session]) -> None:
    """**只列真的變了的欄位**——`null` 會讓地圖重播靜默壞掉。

    前端的累加邏輯是 `if (c.lat !== undefined) cur.lat = c.lat`。
    後端一旦改送 `null`，那個判斷變成 true，座標被設成 null——單位不是消失，
    是被畫到 null 座標。`response_model` 加上去時若忘了 `exclude_none`，
    症狀就是這個，而所有型別檢查照樣綠。
    """
    world = seed_world(session_factory)
    # **一定要真的種出有變動的影格**——沒有事件的話 `frames` 是空的，
    # 下面的迴圈一次都不跑，這條測試就變成空轉（拿掉 `exclude_none` 也不會紅）。
    # 只給座標、不給戰力：這樣 `health`/`strength` 才會是 None，才驗得到那個洞。
    with session_factory() as db:
        for seq, (lat, lng) in enumerate([(23.70, 120.30), (23.71, 120.31)], start=1):
            db.add(
                TacticalEventLog(
                    session_id=world.session_id,
                    seq=seq,
                    tick=seq * 10,
                    event_type="UNIT_MOVED",
                    initiator_id=world.blue_unit_id,
                    weather_snapshot={},
                    terrain_modifier=1.0,
                    ai_decision={},
                    detail={"lat": lat, "lng": lng},
                    prev_hash="",
                    self_hash=f"h{seq}",
                )
            )
        db.commit()

    client = _client(session_factory)
    tok = order_token(world.cmdr_user_id, UserRole.COMMANDER)

    r = client.get(f"/api/v1/sessions/{world.session_id}/aar/replay/states", headers=_hdr(tok))
    assert r.status_code == 200
    body = r.json()
    changed = [c for f in body["frames"] for c in f["changes"]]
    assert changed, "沒有種出任何變動——這條斷言會空轉"
    for frame in body["frames"]:
        for change in frame["changes"]:
            nulls = [k for k, v in change.items() if v is None]
            assert not nulls, f"變動裡出現 null 鍵（前端會誤讀成「有值」）：{nulls}"
    # 底本則相反：`base_lat` 等**可以**是 null（那是「這個單位沒有基準座標」的真實資訊）。
    assert isinstance(body["units"], list)
