"""想定持久化 REST（#7）：存 / 列 / 角色閘 + 由想定開局（建 session + 單位）。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _auth_fakes import auth_header, login, make_client, seed_user
from sqlalchemy.orm import Session, sessionmaker

from app.main import app
from app.models import UserRole

_BUNDLE = {
    "scenario": {
        "name": "測試想定",
        "version": "1.0",
        "bbox": [120.0, 23.0, 122.0, 25.0],
        "mode": "REALTIME",
        "tick_rate_ms": 1000,
        "factions": [{"id": "BLUE", "color": "#3b7dd8"}, {"id": "RED", "color": "#d83b3b"}],
        "relations": [["BLUE", "RED", "HOSTILE"]],
        "victory_conditions": [
            {"faction": "BLUE", "condition": {"type": "eliminate", "target_faction": "RED"}}
        ],
        "files": {"orbat": {"BLUE": "orbat/blue.yaml"}},
    },
    "orbat": {
        "BLUE": {
            "faction": "BLUE",
            "units": [{"designation": "B1", "unit_level": "PLATOON", "lat": 23.7, "lng": 121.0}],
        }
    },
    "msel": {"events": []},
}


@pytest.fixture(autouse=True)
def _clear() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _director_auth(factory: sessionmaker[Session]):  # type: ignore[no-untyped-def]
    seed_user(factory, username="chief", role=UserRole.EXERCISE_DIRECTOR)
    client = make_client(factory)
    return client, auth_header(login(client, "chief")["access_token"])


def test_save_and_list_scenario(session_factory: sessionmaker[Session]) -> None:
    client, hdr = _director_auth(session_factory)
    r = client.post("/api/v1/scenarios", json=_BUNDLE, headers=hdr)
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["name"] == "測試想定" and saved["version"] == "1.0"

    lst = client.get("/api/v1/scenarios", headers=hdr)
    assert lst.status_code == 200
    assert any(s["id"] == saved["id"] for s in lst.json())


def test_save_invalid_bundle_422(session_factory: sessionmaker[Session]) -> None:
    client, hdr = _director_auth(session_factory)
    bad = {"scenario": {"name": "x"}, "orbat": {}}  # 缺必填欄位 → schema 驗證失敗
    r = client.post("/api/v1/scenarios", json=bad, headers=hdr)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "SCENARIO_INVALID"


def test_non_director_forbidden(session_factory: sessionmaker[Session]) -> None:
    seed_user(session_factory, username="joe", role=UserRole.COMMANDER)
    client = make_client(session_factory)
    hdr = auth_header(login(client, "joe")["access_token"])
    assert client.post("/api/v1/scenarios", json=_BUNDLE, headers=hdr).status_code == 403
    assert client.get("/api/v1/scenarios", headers=hdr).status_code == 403


def test_create_session_from_scenario_builds_units(session_factory: sessionmaker[Session]) -> None:
    client, hdr = _director_auth(session_factory)
    sid_scenario = client.post("/api/v1/scenarios", json=_BUNDLE, headers=hdr).json()["id"]
    # 由想定開局
    r = client.post(
        "/api/v1/sessions", json={"name": "x", "scenario_id": sid_scenario}, headers=hdr
    )
    assert r.status_code in (200, 201), r.text
    session_id = r.json()["id"]
    # 全知（EXERCISE_DIRECTOR）→ GET /units 應見想定建出的 B1
    units = client.get(f"/api/v1/sessions/{session_id}/units", headers=hdr).json()
    assert any(u["designation"] == "B1" and u["faction"] == "BLUE" for u in units)
