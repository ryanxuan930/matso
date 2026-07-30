"""自主推演主控台（前端 autonomy.vue）所依賴的後端契約。

這一檔釘的是「前端送得出去、後端收得到、AI 真的看得到」這條鏈——本 repo 反覆出現的病是
中間某一環把值靜默吃掉（欄位未宣告被 pydantic 丟棄、前端漏帶欄位＝重設為預設），
測試全綠但實際沒效果。故每條測試都對應一個具體的斷點。
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from _auth_fakes import auth_header, login, make_client, seed_user
from fakeredis import FakeStrictRedis
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

import app.api.autonomy as autonomy_mod
from app.ai_loop.context import build_faction_context, render_context_prompt
from app.ai_loop.orchestrator import ai_status_key, autonomy_config_key
from app.api import install_error_handlers
from app.factions.relations import FactionRelations, Relation
from app.main import app
from app.models.enums import UserRole


@pytest.fixture(autouse=True)
def _handlers() -> None:
    install_error_handlers(app)


def _fake(monkeypatch: pytest.MonkeyPatch) -> FakeStrictRedis:
    fake = FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(autonomy_mod, "make_redis", lambda *a, **k: fake)
    autonomy_mod._redis.cache_clear()
    return fake


def _white_cell_client(session_factory: sessionmaker[Session]) -> tuple[TestClient, str]:
    seed_user(session_factory, "wc", "pw", role=UserRole.WHITE_CELL_STAFF)
    client = make_client(session_factory)
    return client, login(client, "wc", "pw")["access_token"]


def _stored(fake: FakeStrictRedis, session_id: str) -> dict[str, Any]:
    raw = fake.get(autonomy_config_key(session_id))
    assert raw, "指派未存進 Redis"
    return dict(json.loads(raw))


def test_objectives_survive_put_and_reach_the_prompt(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """抓的病：autonomy.vue 舊版把 objectives 寫死 []，AI 提示詞的「目標/勝負條件」段永遠是空的。

    釘住主控台現在送出的形狀（list[dict]，敘述放 description）能一路存進 Redis、GET 讀回來，
    且 build_faction_context → render_context_prompt 之後文字真的出現在提示詞裡。
    只要有一環把它吃掉，這條就紅。
    """
    fake = _fake(monkeypatch)
    client, token = _white_cell_client(session_factory)
    objectives = [
        {"description": "奪取並確保 218 高地"},
        {"description": "阻絕紅軍沿 3 號公路增援"},
    ]
    r = client.put(
        "/api/v1/sessions/s1/autonomy",
        json={
            "factions": {"BLUE": {"mission": "肅清當面之敵", "objectives": objectives}},
            "heartbeat_s": 45,
        },
        headers=auth_header(token),
    )
    assert r.status_code == 200, r.text
    assert _stored(fake, "s1")["factions"]["BLUE"]["objectives"] == objectives

    got = client.get("/api/v1/sessions/s1/autonomy", headers=auth_header(token))
    assert got.json()["factions"]["BLUE"]["objectives"] == objectives  # 回填編輯畫面用

    # orchestrator 就是這樣把 objectives 交給 worker/context：list(fc.get("objectives") or [])。
    ctx = build_faction_context(
        faction="BLUE",
        tick=0,
        hot_snapshot={},
        unit_meta={},
        known_enemies=[],
        relations=FactionRelations([("BLUE", "RED", Relation.HOSTILE)]),
        objectives=list(got.json()["factions"]["BLUE"]["objectives"]),
        mission="肅清當面之敵",
    )
    text = render_context_prompt(ctx)
    assert "目標/勝負條件" in text
    assert "218 高地" in text
    assert "3 號公路" in text
    app.dependency_overrides.clear()


def test_objectives_must_be_objects_not_bare_strings(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """釘住 objectives 的形狀是 list[dict] 而非 list[str]。

    抓的病：若前端改送字串陣列（直覺寫法），整個 PUT 會 422 ——連陣營指派與心跳都存不進去，
    而畫面上只會出現一句「儲存失敗」。這條讓形狀漂移在後端測試就爆，不用等使用者踩。
    """
    _fake(monkeypatch)
    client, token = _white_cell_client(session_factory)
    r = client.put(
        "/api/v1/sessions/s1/autonomy",
        json={"factions": {"BLUE": {"mission": "m", "objectives": ["奪取山脊"]}}},
        headers=auth_header(token),
    )
    assert r.status_code == 422
    app.dependency_overrides.clear()


def test_ai_ground_truth_roundtrips(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """抓的病（E5）：對照實驗開關後端讀、前端不送——UI 上根本沒有這個開關。

    釘住 true 存得進 Redis 的 ai_config（orchestrator 讀的就是這個鍵）並由 GET 回填勾選狀態。
    """
    fake = _fake(monkeypatch)
    client, token = _white_cell_client(session_factory)
    r = client.put(
        "/api/v1/sessions/s1/autonomy",
        json={
            "factions": {"BLUE": {"mission": "m", "objectives": []}},
            "heartbeat_s": 45,
            "ai_ground_truth": True,
        },
        headers=auth_header(token),
    )
    assert r.status_code == 200, r.text
    assert _stored(fake, "s1")["ai_ground_truth"] is True
    got = client.get("/api/v1/sessions/s1/autonomy", headers=auth_header(token))
    assert got.json()["ai_ground_truth"] is True
    app.dependency_overrides.clear()


def test_put_without_ai_ground_truth_silently_resets_it(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """釘住 E5 的因果：PUT 是整份覆寫，漏帶欄位 = pydantic 補預設 false = 靜默清掉實驗設定。

    這正是舊版前端的病（PUT body 只有 factions/heartbeat_s，白軍按一次儲存就把 curl 設過的
    ai_ground_truth 重設回 false）。行為本身合理（整份覆寫），故測試釘的是「前端每次都必須帶」
    這個前提——哪天有人把欄位從 autonomy.vue 的 body 拿掉，這條會提醒他後果是什麼。
    """
    fake = _fake(monkeypatch)
    client, token = _white_cell_client(session_factory)
    fake.set(autonomy_config_key("s1"), json.dumps({"factions": {}, "ai_ground_truth": True}))
    client.put(
        "/api/v1/sessions/s1/autonomy",
        json={"factions": {"BLUE": {"mission": "m", "objectives": []}}, "heartbeat_s": 45},
        headers=auth_header(token),
    )
    assert _stored(fake, "s1")["ai_ground_truth"] is False
    app.dependency_overrides.clear()


def test_ai_status_exposes_thinking_elapsed_cycles_and_last_submitted(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """抓的病（D-ai）：thinking_since_s / cycles / last_submitted 三欄後端在送、前端零讀取。

    後果是「AI 卡了 5 分鐘」跟「卡了 5 秒」畫面完全一樣。這條釘住三欄確實有值且語義正確：
    thinking_since_s 是**已歷時秒數**（會隨時間變大），last_submitted 是**上一次落單道數**
    （不是時間戳——前端曾被欄位名誤導）。
    """
    fake = _fake(monkeypatch)
    client, token = _white_cell_client(session_factory)
    now = time.time()
    fake.hset(
        ai_status_key("s1"),
        "RED",
        json.dumps(
            {"state": "thinking", "thinking_since": now - 120.0, "heartbeat_s": 45.0, "cycles": 7}
        ),
    )
    fake.hset(
        ai_status_key("s1"),
        "BLUE",
        json.dumps(
            {
                "state": "idle",
                "last_decision_ts": now,
                "heartbeat_s": 45.0,
                "cycles": 9,
                "last_submitted": 3,
            }
        ),
    )
    r = client.get("/api/v1/sessions/s1/ai-status", headers=auth_header(token))
    assert r.status_code == 200, r.text
    facs = {f["faction"]: f for f in r.json()["factions"]}
    # 思考已久 → 前端據此亮「逾時」警示（門檻＝一個心跳）。
    assert facs["RED"]["state"] == "thinking"
    assert facs["RED"]["thinking_since_s"] is not None
    assert facs["RED"]["thinking_since_s"] >= 119.0
    assert facs["RED"]["cycles"] == 7
    assert facs["BLUE"]["cycles"] == 9
    assert facs["BLUE"]["last_submitted"] == 3
    app.dependency_overrides.clear()
