"""`is_visible` 的完整真值表（紅線 3 守門處的回歸釘）。

這支檔案的存在理由：`faction_filter.is_visible` 是傳輸層 fog of war 的**唯一**閘門。
每次要往它加新的受眾維度（WP-B5.2 加席位）之前，先把**現有每一條分支**釘死，
改完再跑一次——只要有任何一格從 False 變 True，就是把敵情漏給了不該看的人。

不測「應該長怎樣」，測「**現在就是這樣**」。
"""

from __future__ import annotations

import pytest

from app.stream.faction_filter import is_visible

# (envelope, faction, omniscient) → 期望可見
CASES: list[tuple[dict, str, bool, bool, str]] = [  # type: ignore[type-arg]
    # ---- 無受眾標籤＝全域事件（如 SESSION_CONCLUDED）----
    ({}, "BLUE", False, True, "無標籤：作戰陣營可見"),
    ({}, "BLUE", True, True, "無標籤：全知可見"),
    # ---- 單一受眾 `faction` ----
    ({"faction": "BLUE"}, "BLUE", False, True, "單一受眾：本人可見"),
    ({"faction": "BLUE"}, "RED", False, False, "單一受眾：他方不可見"),
    ({"faction": "BLUE"}, "RED", True, True, "單一受眾：全知旁通"),
    ({"faction": None}, "RED", False, True, "faction=None 視同全域"),
    # ---- 受眾清單 `factions` ----
    ({"factions": ["BLUE", "RED"]}, "BLUE", False, True, "清單：列內可見"),
    ({"factions": ["BLUE", "RED"]}, "YELLOW", False, False, "清單：列外不可見"),
    ({"factions": ["BLUE"]}, "RED", True, True, "清單：全知旁通"),
    ({"factions": []}, "BLUE", False, False, "空清單：作戰陣營一律不可見"),
    ({"factions": []}, "BLUE", True, True, "空清單＋全知＝WP-C5 的真實副本"),
    # ---- `exclusive`：關掉全知旁通（WP-C5 每陣營投影）----
    ({"factions": ["BLUE"], "exclusive": True}, "BLUE", False, True, "exclusive：列內照常可見"),
    ({"factions": ["BLUE"], "exclusive": True}, "RED", True, False, "exclusive：全知**不**旁通"),
    ({"factions": [], "exclusive": True}, "BLUE", True, False, "exclusive 空清單：誰都收不到"),
]


# ---- WP-B5.2 席位受眾：**只能收窄** ----
# (envelope, faction, omniscient, seat, expected, why)
SEAT_CASES: list[tuple[dict, str, bool, str | None, bool, str]] = [  # type: ignore[type-arg]
    (
        {"faction": "BLUE", "seat": "FSO_FIRES"},
        "BLUE",
        False,
        "FSO_FIRES",
        True,
        "同陣營 + 同席位：收得到",
    ),
    (
        {"faction": "BLUE", "seat": "FSO_FIRES"},
        "BLUE",
        False,
        "S2_INTEL",
        False,
        "同陣營但別席：收不到（席位收窄）",
    ),
    (
        {"faction": "BLUE", "seat": "FSO_FIRES"},
        "BLUE",
        False,
        None,
        False,
        "同陣營但未指派席位：收不到（指定席位的信文不外流）",
    ),
    (
        {"faction": "BLUE", "seat": "FSO_FIRES"},
        "RED",
        False,
        "FSO_FIRES",
        False,
        "**席位相同但陣營不同：收不到**——席位不得繞過陣營判定（紅線 3）",
    ),
    (
        {"faction": "BLUE", "seat": "FSO_FIRES"},
        "RED",
        True,
        "S2_INTEL",
        True,
        "全知：陣營與席位皆旁通（本來就看得到該陣營一切）",
    ),
    (
        {"factions": ["BLUE"], "exclusive": True, "seat": "FSO_FIRES"},
        "RED",
        True,
        "FSO_FIRES",
        False,
        "**exclusive 仍優先**：席位對了也不該讓全知旁通復活",
    ),
    ({"faction": "BLUE"}, "BLUE", False, "S2_INTEL", True, "未指定席位＝該陣營全體"),
]


@pytest.mark.parametrize(("env", "faction", "omni", "seat", "expected", "why"), SEAT_CASES)
def test_seat_audience_only_narrows(
    env: dict,  # type: ignore[type-arg]
    faction: str,
    omni: bool,
    seat: str | None,
    expected: bool,
    why: str,
) -> None:
    assert is_visible(env, faction, omniscient=omni, seat=seat) is expected, why


@pytest.mark.parametrize(("env", "faction", "omni", "expected", "why"), CASES)
def test_seat_param_defaults_do_not_change_existing_behaviour(
    env: dict,  # type: ignore[type-arg]
    faction: str,
    omni: bool,
    expected: bool,
    why: str,
) -> None:
    """不傳 seat 時，行為必須與加席位維度之前逐項相同。"""
    assert is_visible(env, faction, omniscient=omni, seat=None) is expected, why


@pytest.mark.parametrize(("env", "faction", "omni", "expected", "why"), CASES)
def test_audience_truth_table(
    env: dict,  # type: ignore[type-arg]
    faction: str,
    omni: bool,
    expected: bool,
    why: str,
) -> None:
    assert is_visible(env, faction, omniscient=omni) is expected, why


# ---- WP-B5.2 WS 推播端：publish_event 的席位受眾標籤 ----


def test_publish_event_tags_seat_only_when_given() -> None:
    """`publish_event(seat=...)` 才會加 `seat` 標籤；不給就是該陣營全體（既有行為不變）。"""
    import fakeredis

    from app.stream.publish import publish_event

    r = fakeredis.FakeRedis()
    publish_event(r, "s1", "C2_MESSAGE", {"message_id": "m1"}, faction="BLUE")
    publish_event(r, "s1", "C2_MESSAGE", {"message_id": "m2"}, faction="BLUE", seat="FSO_FIRES")
    import json

    from app.state.redis_stream import ring_key

    envs = [json.loads(x) for x in r.lrange(ring_key("s1"), 0, -1)]
    by_msg = {e["payload"]["message_id"]: e for e in envs}
    assert "seat" not in by_msg["m1"], "沒指定席位卻被標上了"
    assert by_msg["m2"]["seat"] == "FSO_FIRES"
    # 兩封都仍帶陣營標籤——席位是額外收窄，不是取代
    assert by_msg["m1"]["faction"] == "BLUE"
    assert by_msg["m2"]["faction"] == "BLUE"
