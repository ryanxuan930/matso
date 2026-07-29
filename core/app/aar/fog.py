"""AAR 的戰場迷霧投影（Backlog 清理，WP-C10.4a 發現）。

**問題**：`GET /aar/*` 對任一參與者開放，而 `read_events` 回的是整包 `ai_decision`。
於是演習**進行中**，任何一個玩家都能 poll AAR 端點，拿到：

- `AREA_FIRE_RESOLVED.losses_by_unit`——敵軍逐單位的真實戰損
- `impacts`——每一發砲彈的落點座標（雙方的）
- 敵軍的交戰、偵測、決策事件

C10.4 把 WS feed 與 AI briefing 兩條路都上了迷霧，這條 REST 卻整個敞著。
少了它，那張卡宣稱的「沒有觀測就沒有戰果」是假的。

**做法**：重用 `broadcaster.event_audience`——**不另寫第三套受眾規則**。
WP-C5 的教訓就是同一套規則散在多處實作，最後其中一處漏掉了 fog of war。

**誰不受限**：全知角色（統裁/白軍）與 `ANALYST`。他們本來就有權看全部，
而 AAR 分析正是 ANALYST 的職務。
"""

from __future__ import annotations

from app.aar.events import AarEvent
from app.state.broadcaster import event_audience, feed_damage
from app.state.ledger import LedgerEvent

# 即使事件屬於自己陣營，也**不下發給玩家**的 `ai_decision` 鍵。
# 這些是裁決的內部量，看得到就等於拿到 ground truth：
# - losses_by_unit：敵軍逐單位真實戰損（BDA 之所以要帶誤差就是為了不給這個）
# - impacts：每一發的落點；比對落點與敵軍位置可反推對方確切座標
# - friendly_losses：單位 id 清單
_FOGGED_DECISION_KEYS = frozenset({"losses_by_unit", "impacts", "friendly_losses"})


def _as_ledger(event: AarEvent) -> LedgerEvent:
    """AarEvent → LedgerEvent，只為了餵給既有的 `event_audience`（欄位同名）。"""
    return LedgerEvent(
        event_type=event.event_type,
        tick=event.tick,
        initiator_id=event.initiator_id,
        target_id=event.target_id,
        ai_decision=event.ai_decision,
        damage_calc=event.damage_calc,
    )


def project_events(
    events: list[AarEvent],
    *,
    faction: str,
    omniscient: bool,
    faction_for: dict[str, str],
) -> list[AarEvent]:
    """把事件流投影成某陣營看得到的版本。

    `omniscient=True`（統裁/白軍/ANALYST）→ 原樣回傳，一個位元都不動。

    否則兩層：
    1. **受眾**：`event_audience` 判不到本陣營的事件整筆剔除（與 WS feed 同一條規則）。
    2. **欄位**：屬於自己的事件也要剝掉裁決內部量與面射擊的傷亡數字
       （`feed_damage`，同 C10.4a 那一條規則）。
    """
    if omniscient:
        return events
    lookup = faction_for.get
    out: list[AarEvent] = []
    for e in events:
        audience = event_audience(_as_ledger(e), lambda uid: lookup(uid, ""))
        if audience is not None and faction not in audience:
            continue
        out.append(
            AarEvent(
                seq=e.seq,
                tick=e.tick,
                event_type=e.event_type,
                initiator_id=e.initiator_id,
                target_id=e.target_id,
                ai_decision={
                    k: v for k, v in e.ai_decision.items() if k not in _FOGGED_DECISION_KEYS
                },
                damage_calc=feed_damage(e.event_type, e.damage_calc),
                reasoning_chain=e.reasoning_chain,
                detail=e.detail,
            )
        )
    return out


__all__ = ["project_events"]
