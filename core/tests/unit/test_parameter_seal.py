"""參數簽證（WP-B4）：雜湊穩定性、403 凍結、篡改拒起、散局不受影響。

驗收條文（SPEC_V2 §6 WP-B4）「凍結後改裝備模板被 403；篡改後 session 重啟被拒且事件留痕；
**未掛演習的散局不受影響**」逐條在此。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from _auth_fakes import auth_header, login, make_client, seed_user
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.governance.guard import params_sealed, seal_violation
from app.governance.seal import build_seal_payload, compute_seal_hash, decompress
from app.main import app
from app.models import EquipmentTemplate, UserRole, WargameSession


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _client(factory: sessionmaker[Session]) -> TestClient:
    seed_user(factory, username="wc", role=UserRole.EXERCISE_DIRECTOR)
    return make_client(factory)


def _h(client: TestClient) -> dict[str, str]:
    return auth_header(login(client, "wc", "pw123")["access_token"])


def _exercise_with_session(client: TestClient, h: dict[str, str]) -> tuple[str, str]:
    ex = client.post("/api/v1/exercises", json={"name": "簽證演習"}, headers=h).json()
    sid = client.post("/api/v1/sessions", json={"name": "掛演習的局"}, headers=h).json()["id"]
    client.post(
        f"/api/v1/exercises/{ex['id']}/sessions",
        json={"session_id": sid, "session_role": "MAIN"},
        headers=h,
    )
    return str(ex["id"]), str(sid)


def _to_rehearsal(client: TestClient, h: dict[str, str], ex_id: str) -> None:
    view = client.get(f"/api/v1/exercises/{ex_id}", headers=h).json()
    for item in view["checklist"]:
        if item["phase"] == "PREP" and item["required"]:
            client.patch(
                f"/api/v1/exercises/{ex_id}/checklist/{item['key']}", json={"done": True}, headers=h
            )
    r = client.patch(f"/api/v1/exercises/{ex_id}/phase", json={"phase": "REHEARSAL"}, headers=h)
    assert r.status_code == 200, r.text


_VALID_KINETIC = {
    "max_range_m": 800,
    "min_range_m": 0,
    "ph_by_range_band": [[400, 0.6], [800, 0.3]],
    "damage_by_armor_class": {"INFANTRY": 30},
    "ammo_types": ["AMMO_X"],
}


def _make_template(client: TestClient, h: dict[str, str], name: str) -> Any:
    return client.post(
        "/api/v1/equipment-templates",
        json={"name": name, "category": "KINETIC", "base_stats": _VALID_KINETIC},
        headers=h,
    )


# ---- 雜湊 ----


def test_hash_is_stable_and_ignores_key_order(session_factory: sessionmaker[Session]) -> None:
    db = session_factory()
    payload = build_seal_payload(db)
    reordered = dict(reversed(list(payload.items())))
    assert compute_seal_hash(payload) == compute_seal_hash(reordered)
    db.close()


def test_hash_covers_the_armoury(session_factory: sessionmaker[Session]) -> None:
    db = session_factory()
    before = compute_seal_hash(build_seal_payload(db))
    db.add(EquipmentTemplate(name="新槍", category="KINETIC", base_stats={"x": 1}))
    db.commit()
    assert compute_seal_hash(build_seal_payload(db)) != before
    db.close()


def test_snapshot_round_trips(session_factory: sessionmaker[Session]) -> None:
    """快照是給事後查證用的——壓縮後解得回來才有意義。"""
    client = _client(session_factory)
    h = _h(client)
    ex_id, _ = _exercise_with_session(client, h)
    _to_rehearsal(client, h, ex_id)
    client.post(f"/api/v1/exercises/{ex_id}/seal", headers=h)

    db = session_factory()
    from app.governance.seal import seal_for

    seal = seal_for(db, ex_id)
    assert seal is not None
    restored = decompress(seal.snapshot_blob)
    assert compute_seal_hash(restored) == seal.content_hash
    assert "sim_params" in restored and "equipment_templates" in restored
    db.close()


# ---- 凍結 ----


def test_sealing_freezes_the_armoury(session_factory: sessionmaker[Session]) -> None:
    """驗收條文：凍結後改裝備模板被 403。"""
    client = _client(session_factory)
    h = _h(client)
    assert _make_template(client, h, "簽證前").status_code == 201  # 先證明本來可以

    ex_id, _ = _exercise_with_session(client, h)
    _to_rehearsal(client, h, ex_id)
    client.post(f"/api/v1/exercises/{ex_id}/seal", headers=h)

    r = _make_template(client, h, "簽證後")
    assert r.status_code == 403
    body = r.json()["error"]
    assert body["code"] == "PARAMS_SEALED"
    # **指名是哪一場演習**：只回 PARAMS_SEALED 的話，操作員不知道要去找誰解鎖。
    assert body["details"]["exercise_name"] == "簽證演習"


def test_sealing_freezes_sim_params_but_not_the_ai_settings(
    session_factory: sessionmaker[Session],
) -> None:
    """`PUT /system/config` 同時寫 ai.*（H 層，規格明說不凍）與 sim（凍結對象）。

    整條擋掉會讓白軍在演習中連 LLM 端點都換不了——那不是規格要求的。
    """
    client = _client(session_factory)
    h = _h(client)
    ex_id, _ = _exercise_with_session(client, h)
    _to_rehearsal(client, h, ex_id)
    client.post(f"/api/v1/exercises/{ex_id}/seal", headers=h)

    ai_only = {"ai_mode": "AI_OFF", "llm_base_url": "http://x", "llm_model": "m"}
    assert client.put("/api/v1/system/config", json=ai_only, headers=h).status_code == 200

    with_sim = {**ai_only, "sim": {"foot_xc_kmh": 9.9}}
    r = client.put("/api/v1/system/config", json=with_sim, headers=h)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "PARAMS_SEALED"


def test_resending_identical_sim_params_is_not_a_violation(
    session_factory: sessionmaker[Session],
) -> None:
    """前端每次存檔都送整包。原封不動的重送變成 403 會讓設定頁完全不能用。"""
    client = _client(session_factory)
    h = _h(client)
    base = {"ai_mode": "AI_OFF", "llm_base_url": "http://x", "llm_model": "m"}
    current = client.get("/api/v1/system/config", headers=h).json()
    same = {**base, "sim": current["sim"]}
    assert client.put("/api/v1/system/config", json=same, headers=h).status_code == 200

    ex_id, _ = _exercise_with_session(client, h)
    _to_rehearsal(client, h, ex_id)
    client.post(f"/api/v1/exercises/{ex_id}/seal", headers=h)
    assert client.put("/api/v1/system/config", json=same, headers=h).status_code == 200


def test_unsealing_unlocks_and_leaves_a_trace(session_factory: sessionmaker[Session]) -> None:
    """沒有解鎖路徑的話，一場被忘記的演習會讓全域武器庫永遠唯讀。"""
    client = _client(session_factory)
    h = _h(client)
    ex_id, _ = _exercise_with_session(client, h)
    _to_rehearsal(client, h, ex_id)
    client.post(f"/api/v1/exercises/{ex_id}/seal", headers=h)
    assert _make_template(client, h, "鎖住時").status_code == 403

    assert client.delete(f"/api/v1/exercises/{ex_id}/seal", headers=h).status_code == 204
    assert _make_template(client, h, "解鎖後").status_code == 201
    audit = client.get(f"/api/v1/exercises/{ex_id}/audit", headers=h).json()
    actions = [a["action"] for a in audit]
    assert "PARAMS_SEALED" in actions and "PARAMS_UNSEALED" in actions


def test_review_phase_releases_the_lock(session_factory: sessionmaker[Session]) -> None:
    """演習結束後（REVIEW）解鎖——檢討期間還鎖著沒有意義。"""
    client = _client(session_factory)
    h = _h(client)
    ex_id, _ = _exercise_with_session(client, h)
    _to_rehearsal(client, h, ex_id)
    client.post(f"/api/v1/exercises/{ex_id}/seal", headers=h)
    # `rehearsal_done` 也是離開 REHEARSAL 的必要項（`params_sealed` 已由簽證自動勾）。
    client.patch(
        f"/api/v1/exercises/{ex_id}/checklist/rehearsal_done", json={"done": True}, headers=h
    )
    for phase in ("EXECUTION", "REVIEW"):
        r = client.patch(f"/api/v1/exercises/{ex_id}/phase", json={"phase": phase}, headers=h)
        assert r.status_code == 200, r.text
    assert _make_template(client, h, "檢討期").status_code == 201


# ---- 篡改偵測 ----


def test_tampering_refuses_the_session_start(session_factory: sessionmaker[Session]) -> None:
    """驗收條文：篡改後 session 重啟被拒。"""
    client = _client(session_factory)
    h = _h(client)
    ex_id, sid = _exercise_with_session(client, h)
    _to_rehearsal(client, h, ex_id)
    client.post(f"/api/v1/exercises/{ex_id}/seal", headers=h)

    db = session_factory()
    assert seal_violation(db, sid) is None  # 沒動過 → 可起
    # 繞過 API 直接改庫（那正是「偷改參數重啟」的手法）。
    db.add(EquipmentTemplate(name="偷加的", category="KINETIC", base_stats={}))
    db.commit()
    violation = seal_violation(db, sid)
    assert violation is not None and "簽證不符" in violation
    db.close()


def test_seal_view_shows_the_mismatch_before_anyone_restarts(
    session_factory: sessionmaker[Session],
) -> None:
    """白軍要看得出「現在的參數還跟簽證時一樣嗎」，而不是等到開局被拒才發現。"""
    client = _client(session_factory)
    h = _h(client)
    ex_id, _ = _exercise_with_session(client, h)
    _to_rehearsal(client, h, ex_id)
    client.post(f"/api/v1/exercises/{ex_id}/seal", headers=h)
    assert client.get(f"/api/v1/exercises/{ex_id}/seal", headers=h).json()["matches"] is True

    db = session_factory()
    db.add(EquipmentTemplate(name="偷加的", category="KINETIC", base_stats={}))
    db.commit()
    db.close()
    view = client.get(f"/api/v1/exercises/{ex_id}/seal", headers=h).json()
    assert view["matches"] is False
    assert view["current_hash"] != view["content_hash"]


# ---- 散局不受影響 ----


def test_standalone_sessions_still_start(session_factory: sessionmaker[Session]) -> None:
    """驗收條文：**未掛演習的散局不受影響**——講的是開局不被拒。"""
    client = _client(session_factory)
    h = _h(client)
    ex_id, _ = _exercise_with_session(client, h)
    standalone = client.post("/api/v1/sessions", json={"name": "散局"}, headers=h).json()["id"]
    _to_rehearsal(client, h, ex_id)
    client.post(f"/api/v1/exercises/{ex_id}/seal", headers=h)

    db = session_factory()
    db.add(EquipmentTemplate(name="偷加的", category="KINETIC", base_stats={}))
    db.commit()
    assert seal_violation(db, standalone) is None  # 散局照跑
    db.close()


def test_no_exercise_means_no_seal_at_all(session_factory: sessionmaker[Session]) -> None:
    """系統裡沒有演習 → 完全沒有凍結（既有部署零行為變更）。"""
    client = _client(session_factory)
    h = _h(client)
    assert _make_template(client, h, "自由").status_code == 201
    db = session_factory()
    assert params_sealed(db) is False
    db.close()


def test_seeding_does_not_overwrite_a_sealed_armoury(
    session_factory: sessionmaker[Session],
) -> None:
    """**這條擋的是一個很難查的自傷**。

    `seed_equipment._upsert_templates` 跑在每一次由想定開局時，且它會**覆寫**既有範本的
    `base_stats`。簽證期間若照常覆寫，在已簽證的演習裡新開一個預推局，就會靜靜改掉被鎖住的
    那張表——然後**該演習的每一局都會因為雜湊不符而拒起**，而且沒有任何操作留下痕跡。
    """
    from app.adjudication.seed_equipment import ensure_weapon_templates

    client = _client(session_factory)
    h = _h(client)
    ex_id, _ = _exercise_with_session(client, h)
    _to_rehearsal(client, h, ex_id)

    db = session_factory()
    ensure_weapon_templates(db)
    db.commit()
    tmpl = db.query(EquipmentTemplate).filter(EquipmentTemplate.name == "RIFLE_556").one_or_none()
    assert tmpl is not None
    tmpl.base_stats = {"tweaked": True}  # 白軍在簽證前調過的值
    db.commit()
    db.close()

    client.post(f"/api/v1/exercises/{ex_id}/seal", headers=h)

    db = session_factory()
    ensure_weapon_templates(db)  # 新局開起來 → seed 又跑一次
    db.commit()
    again = db.query(EquipmentTemplate).filter(EquipmentTemplate.name == "RIFLE_556").one_or_none()
    assert again is not None
    assert again.base_stats == {"tweaked": True}, "簽證期間 seed 不得覆寫既有範本"
    db.close()


def test_session_of_a_sealed_exercise_starts_when_nothing_changed(
    session_factory: sessionmaker[Session],
) -> None:
    """凍結本身不該把演習自己的局擋在門外。"""
    client = _client(session_factory)
    h = _h(client)
    ex_id, sid = _exercise_with_session(client, h)
    _to_rehearsal(client, h, ex_id)
    client.post(f"/api/v1/exercises/{ex_id}/seal", headers=h)
    db = session_factory()
    assert seal_violation(db, sid) is None
    assert db.get(WargameSession, sid) is not None
    db.close()
