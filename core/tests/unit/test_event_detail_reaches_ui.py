"""`LedgerEvent.detail` 要送得到操作員手上（UI 缺口盤點 P1）。

## 為什麼這一檔存在

引擎最會講話的那一半事件——移動、工兵、後勤——把「為什麼」全記在 `detail` 裡：
油量剩多少、卡在哪一格、觸到哪一張雷區、耗損是行軍磨的還是強穿付的代價。
而 `detail` **一個字都沒有被轉發**：WS 串流的信封過濾掉、AAR 匯出也沒帶。
於是那些資訊在整個系統裡**沒有任何操作員取得得到的路徑**。

最能說明問題的證據：`useCopFeed.ts` 的 `REASON_LABELS` 裡有五條翻譯——
OUT_OF_FUEL / IMPASSABLE_TERRAIN / MARCH / FORCED_CROSSING / TARGET_GONE——
在 feed 上**永遠不可能被觸發**。有人認真寫了翻譯，只是線沒接上。

## 這一檔守的兩件事

1. 白名單裡的鍵**真的送出去**（不然翻譯又要繼續躺著）。
2. `lat` / `lng` **絕對不送**——那是單位真實座標，送出去會繞過 WP-C5 的位置凍結，
   而 STATE_DIFF 那一側費了大力氣在 `public_diff` 把它剝掉。
"""

from __future__ import annotations

import json

from app.aar.events import AarEvent
from app.aar.export import export_json
from app.state.broadcaster import build_event_envelope
from app.state.ledger import LedgerEvent


def _halted_by_fuel() -> LedgerEvent:
    """真實形狀：`engine/movement.py` 油乾停駛時寫的那一筆。"""
    return LedgerEvent(
        event_type="MOVE_HALTED_FUEL",
        tick=42,
        initiator_id="u-1",
        detail={
            "order_id": "o-1",
            "reason": "OUT_OF_FUEL",
            "profile": "TRACKED",
            "fuel_remaining": 0.0,
            "fuel_burn_per_km": 4.5,
            # ⚠ 移動事件一定帶座標——它們是位置凍結要保護的東西。
            "lat": 23.7,
            "lng": 120.3,
        },
    )


def test_the_reason_actually_reaches_the_feed() -> None:
    """`REASON_LABELS['OUT_OF_FUEL']` 終於有機會被觸發。"""
    payload = build_event_envelope(_halted_by_fuel())["payload"]
    assert payload["reason"] == "OUT_OF_FUEL", (
        f"停駛原因沒有送到 feed，payload 只有 {sorted(payload)}"
        "——前端那條「燃料耗盡」的翻譯永遠不會被觸發"
    )
    assert payload["fuel_remaining"] == 0.0
    assert payload["profile"] == "TRACKED"


def test_raw_positions_are_never_broadcast() -> None:
    """**整包轉發 `detail` 會漏座標。** 這條就是白名單存在的理由。

    `movement.py` 的每一步都把 lat/lng 寫進 detail。若照單全收，
    斷聯敵軍的即時真實座標就這樣出去了——而 WP-C5 的位置凍結正是在擋這件事。
    """
    payload = build_event_envelope(_halted_by_fuel())["payload"]
    assert "lat" not in payload and "lng" not in payload, (
        f"單位真實座標被廣播出去了（{payload.get('lat')}, {payload.get('lng')}）——位置凍結被繞過"
    )


def test_ai_decision_still_wins_when_both_carry_the_same_key() -> None:
    """兩邊都有同一個鍵時以 `ai_decision` 為準——它是裁決的產物，detail 是診斷欄。"""
    event = LedgerEvent(
        event_type="ENGAGEMENT_RESOLVED",
        tick=1,
        ai_decision={"reason": "NO_AMMO"},
        detail={"reason": "TARGET_GONE"},
    )
    assert build_event_envelope(event)["payload"]["reason"] == "NO_AMMO"


def test_aar_export_carries_detail() -> None:
    """匯出是分析要拿出去跑的原始資料，`detail` 是最有訊息量的一欄。"""
    rows = json.loads(
        export_json(
            [
                AarEvent(
                    seq=1,
                    tick=42,
                    event_type="MOVE_ATTRITION",
                    initiator_id="u-1",
                    target_id=None,
                    detail={"reason": "FORCED_CROSSING", "distance_km": 4.0},
                )
            ]
        )
    )
    assert rows[0]["detail"]["reason"] == "FORCED_CROSSING", (
        f"匯出沒帶 detail：{sorted(rows[0])}——耗損是行軍磨的還是強穿付的代價，分析拿不到"
    )


def test_anonymised_export_still_omits_detail() -> None:
    """匿名匯出要繼續省略——`detail` 帶 lat/lng 與 order_id，足以把匿名標籤還原。"""
    rows = json.loads(
        export_json(
            [
                AarEvent(
                    seq=1,
                    tick=1,
                    event_type="UNIT_MOVED",
                    initiator_id="u-1",
                    target_id=None,
                    detail={"lat": 23.7, "lng": 120.3, "order_id": "o-1"},
                )
            ],
            anonymize=True,
        )
    )
    assert "detail" not in rows[0], "匿名匯出帶了 detail，匿名化就失去意義了"
