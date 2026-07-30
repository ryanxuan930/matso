"""狀態廣播（STATE_DIFF）— Redis 落地層（SPEC_FULL §16.2、contracts/ws_protocol.md）。

本層負責把每 tick 的 diff 包成 envelope 並：
1. 指派 per-session 單調 seq（Redis INCR）。
2. 推入 ring buffer（Redis list，capped 5000）供斷線重連補送。
3. PUBLISH 到 pub/sub 頻道。

WebSocket 客戶端 fan-out（訂閱頻道、依 faction 過濾、推給前端）屬 O4.3，不在此。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Container, Mapping, Sequence
from typing import Any

import redis

from app.comms import REPORT_LAT_KEY, REPORT_LNG_KEY, REPORT_TICK_KEY, project_position
from app.fires.survivability import MISSION_COUNT_KEY
from app.state.hot_state import SessionDiff, UnitDiff, session_tick_key
from app.state.ledger import LedgerEvent
from app.state.redis_stream import channel_key, publish_to_stream, ring_key, seq_key

RING_CAPACITY = 5000  # §16.2：保留最近 5000 條供重連補送

# 不進 WS 戰況事件 feed 的事件型別：UNIT_MOVED 每 tick 每移動單位一則（過吵，位置改由 STATE_DIFF
# 呈現）；TICK_OVERRUN 為診斷。其餘（ENGAGEMENT_RESOLVED / UNIT_ARRIVED / 注入 / 觸發…）皆推。
_FEED_EXCLUDE = frozenset({"UNIT_MOVED", "TICK_OVERRUN"})


FactionLookup = Callable[[str], str]


def event_audience(event: LedgerEvent, faction_for: FactionLookup | None) -> list[str] | None:
    """事件的受眾陣營（fog of war）。回 None ＝ 全域事件（所有人可見）。

    規則：
    - **`observer_faction` 存在時它就是唯一受眾**（SENSOR_CONTACT）。這條必須優先——該事件的
      `target_id` 是「被偵測到的單位」，若按 unit 推導受眾，等於通知對方「你被發現了」。
    - 否則取事件所涉單位的陣營（射手/目標）。一次交戰同時關乎兩方，故是清單。
    - 都沒有（SESSION_CONCLUDED / FACTION_RELATION_CHANGED / TICK_OVERRUN…）→ None ＝全域。

    `faction_for` 為 None（測試/合成想定）→ 一律 None，維持既有「全部廣播」行為不變。
    """
    if faction_for is None:
        return None
    decision = event.ai_decision if isinstance(event.ai_decision, dict) else {}
    observer = decision.get("observer_faction")
    if isinstance(observer, str) and observer:
        return [observer]
    factions = {
        f for uid in (event.initiator_id, event.target_id) if uid for f in (faction_for(uid),) if f
    }
    return sorted(factions) or None


# 傷亡數字**不得投影給玩家**的事件型別（WP-C10.4）。
# 間瞄火力打的是看不見的地方——沒有前觀就不該知道打死了幾個。帳本上的 damage_calc
# 仍是真值（AAR 要真的），這裡擋的是「即時回饋」這條路。
# 直射的 ENGAGEMENT_RESOLVED 不在此列：打得到就看得到，那是刻意的差別。
_DAMAGE_FOG = frozenset({"AREA_FIRE_RESOLVED"})


def feed_damage(event_type: str, damage_calc: float | None) -> float | None:
    """投影給玩家/AI 的傷亡數字。屬迷霧型別 → None（不是 0——那會被讀成「沒打中」）。

    **兩個投影邊界都要呼叫它**：WS 戰況 feed（本檔）與 AI briefing
    （`ai_loop/world_view._event_summary`）。只補其中一個的話，人看不到但 LLM 指揮官
    仍握有完美戰果評估——那種不對稱比全部洩漏更難察覺。
    """
    if event_type in _DAMAGE_FOG:
        return None
    return damage_calc


def build_event_envelope(
    event: LedgerEvent, faction_for: FactionLookup | None = None
) -> dict[str, Any]:
    """把 LedgerEvent 壓成精簡的 EVENT envelope payload（供戰況 feed；ID→番號由前端對映）。"""
    payload: dict[str, Any] = {"event_type": event.event_type, "tick": event.tick}
    if event.initiator_id:
        payload["initiator_id"] = event.initiator_id
    if event.target_id:
        payload["target_id"] = event.target_id
    damage = feed_damage(event.event_type, event.damage_calc)
    if damage is not None:
        payload["damage"] = damage
    # #33 comms 狀態轉移的 from/to 也帶出（供戰況 feed 顯示「通聯 X→Y」）。
    # mode＝COMBINED/VOLLEY/AGGREGATE（供 feed 標示交戰型態，SPEC_EXTEND P4）。
    # reason_detail＝聯合兵種被拒時逐武器原因彙總（供 feed 顯示為何整組不能打）。
    # winners＝SESSION_CONCLUDED 勝方（O11.5，供前端勝負橫幅）。
    for k in (
        "status",
        "reason",
        "reason_detail",
        "target_health_after",
        "from",
        "to",
        "mode",
        "winners",
        # WP-C10.4：面射擊的觀測狀態（射方自己的資訊：那個點上有沒有我的眼睛）+ 發數。
        "observation",
        "rounds",
        # WP-C10.4b BDA：估計值與誤差帶。**`is_estimate` 一定要一起帶**——
        # 前端據它決定永遠不把這個數字呈現成真值。
        "estimated_losses",
        "is_estimate",
        "error_band",
        # WP-C9 誤傷：沒列進這個 allowlist 的話，FRATRICIDE 到了 COP 只剩一個
        # 沒有內容的空殼（型別對、什麼都看不到）。
        "cause",
        "shooter_faction",
    ):
        if isinstance(event.ai_decision, dict) and k in event.ai_decision:
            payload[k] = event.ai_decision[k]
    envelope: dict[str, Any] = {"v": 1, "seq": 0, "type": "EVENT", "payload": payload}
    audience = event_audience(event, faction_for)
    if audience is not None:
        envelope["factions"] = audience  # fog of war：僅相關陣營（+全知）收得到
    return envelope


def build_state_diff_envelope(
    seq: int,
    tick: int,
    diff: SessionDiff,
    *,
    factions: list[str] | None = None,
    exclusive: bool = False,
) -> dict[str, Any]:
    """依 ws_protocol.md 的 envelope + STATE_DIFF payload 格式建構訊息。

    `factions` / `exclusive` 是受眾標籤（見 ws_protocol.md「受眾標籤」）：
    每陣營投影用 `factions=[F], exclusive=True`；真實副本用 `factions=[]`（只有全知旁通收得到）。
    """
    envelope: dict[str, Any] = {
        "v": 1,
        "seq": seq,
        "tick": tick,
        "type": "STATE_DIFF",
        "payload": {"units": [{"id": unit_id, **fields} for unit_id, fields in diff.items()]},
    }
    if factions is not None:
        envelope["factions"] = factions
        if exclusive:
            envelope["exclusive"] = True
    return envelope


# 位置回報的原始欄位是**投影的輸入**，不是要下發的狀態——契約沒有它們，且對陣營副本而言
# 就是「凍結前的真實位置」。一律剝掉（含全知的真實副本：統裁看 lat/lng 就好）。
# ⚠ 這是 **denylist**：任何新的熱狀態鍵一被寫入就會自動出現在 STATE_DIFF 裡。
# WP-C10.5 的陣地變換計數是引擎內部帳，沒有任何 client 消費者 → 剝掉。
_INTERNAL_FIELDS = frozenset({REPORT_LAT_KEY, REPORT_LNG_KEY, REPORT_TICK_KEY, MISSION_COUNT_KEY})


def _public_fields(fields: UnitDiff) -> UnitDiff:
    return {k: v for k, v in fields.items() if k not in _INTERNAL_FIELDS}


def public_diff(diff: SessionDiff) -> SessionDiff:
    """真實副本（全知視角）：只剝內部欄位，不套任何投影。"""
    out: SessionDiff = {}
    for unit_id, fields in diff.items():
        public = _public_fields(fields)
        if public:
            out[unit_id] = public
    return out


def project_diff(
    diff: SessionDiff,
    *,
    visible: Container[str],
    faction_for: FactionLookup,
    state_for: Callable[[str], Mapping[str, Any] | None],
) -> SessionDiff:
    """某陣營視角的 STATE_DIFF 投影（WP-C5）——兩層 fog of war，皆為後端強制（紅線 3）。

    1. **可見集**：陣營不在 `visible`（自己＋盟軍）的單位整筆剔除。WP-C5 之前 STATE_DIFF
       沒有任何受眾標籤，等於把敵軍即時座標廣播給每個連線的 client。
    2. **位置凍結**（SPEC §6.2）：通聯非 ONLINE 的單位以最後一次位置回報取代 lat/lng。
       **尚無回報時是移除 lat/lng 而非送 null**——null 會把 client 上最後已知的位置清掉，
       等於「單位憑空消失」；移除則讓 client 保留最後已知值，那正是凍結的語義。

    `stale_since_tick` 只在通聯狀態**本 tick 有變動**時才附上（含恢復 ONLINE 時送 None 清標記）；
    否則每 tick 對每個斷聯單位重複同一個值，純屬雜訊。
    """
    out: SessionDiff = {}
    for unit_id, fields in diff.items():
        owner = faction_for(unit_id)
        if owner and owner not in visible:
            continue
        projected = _public_fields(fields)
        transition = "comms_state" in fields
        position = project_position(state_for(unit_id) or {})
        if position is None:  # ONLINE：真實座標照送
            if transition:
                projected["stale_since_tick"] = None  # 恢復通聯 → 清掉 client 的凍結標記
        else:
            projected.pop("lat", None)
            projected.pop("lng", None)
            if position.lat is not None and position.lng is not None:
                projected["lat"], projected["lng"] = position.lat, position.lng
            if transition:
                projected["stale_since_tick"] = position.stale_since_tick
        if projected:
            out[unit_id] = projected
    return out


def build_clock_envelope(seq: int, tick: int) -> dict[str, Any]:
    """CLOCK 心跳 envelope（頂層 tick）——閒置（無 STATE_DIFF）時仍讓前端牆鐘不凍結。"""
    return {"v": 1, "seq": seq, "tick": tick, "type": "CLOCK", "payload": {}}


# CLOCK 心跳節流：閒置時每 N tick 送一次（避免灌爆 ring；有活動時 STATE_DIFF 已逐 tick 更新）。
_CLOCK_EVERY_TICKS = 5


class RedisBroadcaster:
    """把 STATE_DIFF 寫入 Redis ring buffer 並 publish。滿足 Kernel 的 Broadcaster 介面。

    - redis-py 為同步 driver：publish 內以 asyncio.to_thread 執行，不阻塞 event loop
      （HOW_TO §3.1；O1.7/R9）。
    - seq 語意（O1.7/R7）：broadcast seq 是「傳輸層計數器」，存於 Redis、**不耐 Redis 清空**。
      Redis 遺失 = ring buffer 同時遺失 → 所有客戶端必須全量重同步；復原流程應呼叫
      reset_stream() 讓新串流從乾淨狀態開始（契約見 contracts/ws_protocol.md）。
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        session_id: str,
        faction_for: FactionLookup | None = None,
        *,
        observers: Sequence[str] = (),
        visible_for: Callable[[str], Container[str]] | None = None,
        state_for: Callable[[str], Mapping[str, Any] | None] | None = None,
    ) -> None:
        self._redis = redis_client
        self._session_id = session_id
        # fog of war：unit → faction，用來標事件受眾。None（測試/合成想定）→ 不標＝維持全廣播。
        self._faction_for = faction_for
        # WP-C5 每陣營 STATE_DIFF 投影所需的三件事：有哪些觀測陣營、各自看得到誰、
        # 以及單位當下的熱狀態（判位置凍結）。三者不齊 → 退回單一全廣播信封（測試/合成想定）。
        self._observers = tuple(observers)
        self._visible_for = visible_for
        self._state_for = state_for

    def _seq_key(self) -> str:
        return seq_key(self._session_id)

    def _ring_key(self) -> str:
        return ring_key(self._session_id)

    def _channel(self) -> str:
        return channel_key(self._session_id)

    def _tick_key(self) -> str:
        return session_tick_key(self._session_id)

    def _write_tick(self, tick: int) -> None:
        # 供 API 下令端讀「當前 sim tick」以戳記 issued_at_tick（否則永遠 0 → 指令無法依時排序、
        # 前端顯示 T0）。單一寫者＝本廣播器（Kernel 執行緒）；API 唯讀，不違反 single-writer。
        self._redis.set(self._tick_key(), tick)

    async def publish(self, tick: int, diff: SessionDiff) -> None:
        if not diff:
            # 閒置無變動：節流送 CLOCK 心跳，讓前端牆鐘不凍結（否則 idle session tick 停在 T—）。
            if tick % _CLOCK_EVERY_TICKS == 0:
                await asyncio.to_thread(self._publish_clock_sync, tick)
            return
        await asyncio.to_thread(self._publish_sync, tick, diff)

    def _publish_clock_sync(self, tick: int) -> None:
        self._write_tick(tick)
        publish_to_stream(
            self._redis,
            seq_key=self._seq_key(),
            ring_key=self._ring_key(),
            channel=self._channel(),
            envelope=build_clock_envelope(0, tick),  # seq 佔位，由 publish_to_stream 指派
            ring_capacity=RING_CAPACITY,
        )

    async def publish_events(self, events: Sequence[LedgerEvent]) -> None:
        """把裁決事件推到 WS 事件流（戰況 feed）。與 STATE_DIFF 共用 seq/ring/channel（原子）。"""
        feed = [e for e in events if e.event_type not in _FEED_EXCLUDE]
        if feed:
            await asyncio.to_thread(self._publish_events_sync, feed)

    def _publish_events_sync(self, events: list[LedgerEvent]) -> None:
        for e in events:
            publish_to_stream(
                self._redis,
                seq_key=self._seq_key(),
                ring_key=self._ring_key(),
                channel=self._channel(),
                envelope=build_event_envelope(e, self._faction_for),
                ring_capacity=RING_CAPACITY,
            )

    def _projecting(self) -> bool:
        """具備每陣營投影的條件（缺任一件就退回單一全廣播信封，維持舊行為）。"""
        return bool(self._observers and self._visible_for and self._faction_for and self._state_for)

    def _envelopes(self, tick: int, diff: SessionDiff) -> list[dict[str, Any]]:
        """本 tick 要發的 STATE_DIFF 信封（seq 佔位 0，由 publish_to_stream 指派）。"""
        if not self._projecting():
            return [build_state_diff_envelope(0, tick, diff)]
        assert self._visible_for is not None and self._faction_for is not None
        assert self._state_for is not None
        out: list[dict[str, Any]] = []
        # 真實副本：`factions: []` ＝ 沒有作戰陣營在受眾內，只有全知旁通收得到。
        truth = public_diff(diff)
        if truth:
            out.append(build_state_diff_envelope(0, tick, truth, factions=[]))
        for faction in self._observers:
            projected = project_diff(
                diff,
                visible=self._visible_for(faction),
                faction_for=self._faction_for,
                state_for=self._state_for,
            )
            if projected:
                out.append(
                    build_state_diff_envelope(
                        0, tick, projected, factions=[faction], exclusive=True
                    )
                )
        return out

    def _publish_sync(self, tick: int, diff: SessionDiff) -> None:
        # 原子指派 seq + 寫 ring + publish（CODE_REVIEW C3）——與 API 端 publish_event 共用同一
        # 原子路徑，避免兩個寫入者交錯造成 ring 順序與 seq 不一致。
        self._write_tick(tick)  # 下令端讀此戳記 issued_at_tick（活動 tick 每次刷新）
        for envelope in self._envelopes(tick, diff):
            publish_to_stream(
                self._redis,
                seq_key=self._seq_key(),
                ring_key=self._ring_key(),
                channel=self._channel(),
                envelope=envelope,
                ring_capacity=RING_CAPACITY,
            )

    def reset_stream(self) -> None:
        """清除傳輸層狀態（seq 計數器 + ring buffer），供崩潰復原後重啟乾淨串流。

        呼叫後 seq 從 1 重新起算；WS 層（O4.3）看到客戶端 last_seq 超出 ring 範圍
        時回 RESYNC_REQUIRED（含 seq 倒退情形），客戶端走全量重同步。
        """
        self._redis.delete(self._seq_key(), self._ring_key())


class CollectingBroadcaster:
    """測試用 broadcaster：記錄每次 publish 的 (tick, diff)，不接 Redis。"""

    def __init__(self) -> None:
        self.published: list[tuple[int, SessionDiff]] = []
        self.published_events: list[LedgerEvent] = []

    async def publish(self, tick: int, diff: SessionDiff) -> None:
        self.published.append((tick, dict(diff)))

    async def publish_events(self, events: Sequence[LedgerEvent]) -> None:
        self.published_events.extend(events)
