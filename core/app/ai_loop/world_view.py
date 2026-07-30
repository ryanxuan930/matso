"""AI 陣營的「可見世界」投影 — WP-A1（SPEC_V2 §6 WP-A1）。

`context.py` 是**零 I/O 的純投影**（其 docstring 明訂），故所有要讀 DB 的可見性查詢集中在本模組：
敵情（IntelContact）、盟軍（units 共享視圖）、近期事件（Ledger 受眾過濾）。

紅線（fog of war，SPEC_FULL §13.3 / 紅線 #3）：**過濾一律在後端、且只用該陣營看得到的資料**。
本模組不得以 ground truth 回填 AI 看不到的東西——那正是 WP-A1 要消滅的問題
（改版前 `ground_truth_enemies` 讓 AI 全知敵方位置）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.comms import (
    IntelGranularity,
    faction_link_state,
    intel_granularity,
    parse_link_state,
    project_position,
)
from app.factions.relations import FactionRelations
from app.intel import store
from app.intel.service import IntelService
from app.models import TacticalEventLog, TacticalUnit
from app.state.broadcaster import event_audience, feed_damage
from app.state.hot_state import UnitState
from app.state.ledger import LedgerEvent

# 近期事件：不進 AI briefing 的事件型別。
# - UNIT_MOVED/TICK_OVERRUN：與 WS feed 同一組雜訊（每 tick 每單位一則／診斷用）。
# - SENSOR_CONTACT：受眾雖是觀測方，但其 `target_id` 是**被偵測單位的真實 id**（ground truth
#   連結，`intel/schemas.py` 明訂永不下發）。敵情已由 known_enemies 呈現，這裡放行只會把
#   去識別化在事件欄位上破功。
_EVENT_EXCLUDE = frozenset({"UNIT_MOVED", "TICK_OVERRUN", "SENSOR_CONTACT"})
_EVENT_OVERSCAN = 10  # 受眾過濾會刷掉他方事件 → 先多抓幾倍再截尾（見 recent_events）
_EVENT_SCAN_CAP = 400  # 單次掃描硬上限：避免長局把整條帳本拉進記憶體


def projected_snapshot(snapshot: Mapping[str, UnitState]) -> dict[str, UnitState]:
    """把熱狀態快照套上**位置回報投影**（WP-C5）——AI 指揮官與人類指揮官看到同一張圖。

    純函數（不讀 DB/Redis）。對每個單位：通聯非 ONLINE 就把 `lat`/`lng` 換成它最後一次
    位置回報，並補 `stale_since_tick`；尚無回報則**移除** `lat`/`lng`（context 會渲染成
    「位置未知」，不得以真實座標回填——那正是本卡要消滅的全知）。

    這裡刻意投影**整份快照**而非只有己方：`allied_units` 也吃這份資料，而盟軍的位置同樣
    是經該軍的回報鏈路來的（`GET /units` 的陣營視角也是對己方＋盟軍一律套用）。
    敵方單位不經此路徑（AI 的敵情走 `contacts_from_intel`）。
    """
    out: dict[str, UnitState] = {}
    for uid, state in snapshot.items():
        projection = project_position(state)
        if projection is None:
            out[uid] = dict(state)
            continue
        projected = dict(state)
        projected.pop("lat", None)
        projected.pop("lng", None)
        if projection.lat is not None and projection.lng is not None:
            projected["lat"], projected["lng"] = projection.lat, projection.lng
        projected["stale_since_tick"] = projection.stale_since_tick
        out[uid] = projected
    return out


def faction_granularity(
    snapshot: Mapping[str, UnitState], unit_meta: Mapping[str, Any], faction: str
) -> IntelGranularity:
    """該陣營的敵情粒度（WP-C5）——由**自己單位**的通聯狀態導出，與 `GET /intel` 同一規則。

    純函數：熱狀態快照已在手上，不必像 REST 端那樣再讀一次 Redis。
    """
    links = [
        parse_link_state(snapshot.get(uid, {}).get("comms_state"))
        for uid, meta in unit_meta.items()
        if getattr(meta, "faction", None) == faction
    ]
    return intel_granularity(faction_link_state(links))


def contacts_from_intel(
    db: Session,
    session_id: str,
    faction: str,
    relations: FactionRelations,
    granularity: IntelGranularity = IntelGranularity.FULL,
) -> list[dict[str, Any]]:
    """該陣營**真實偵測所得**的敵情（EnemyVisibility 協定實作）。

    投影完全複用 `IntelService.visible_contacts`——即 `GET /intel` 的後端過濾與 fidelity 分級
    （DETECTED 只有位置/時間戳；CLASSIFIED 加型別；IDENTIFIED 再加番號與陣營）。**不重寫過濾邏輯**，
    避免兩份真相。`relations` 不參與：盟軍在 sweep 階段就不成為 contact（#91 共享視圖語義）。

    **`unit_id` 是刻意帶上的**：ENGAGE 令的橋接（`orders_bridge`）與物理預檢都以真實
    `TacticalUnit.id` 查目標，AI 必須給得出它，否則接上迷霧後 AI 就再也打不了任何目標。
    這不額外洩漏身分——contact 對同一目標是 upsert、`contact_id` 本身即穩定識別，兩者的關聯能力
    相同；真正的敵情內容仍由 fidelity 閘門控制。（後續若要消除「穩定 id 可跨時關聯」這個殘留，
    需輪替識別碼，屬另一張卡。）

    contact 是**最後已知位置**：目標移走或已被殲滅仍會留在清單裡（IntelContact 無存活狀態、
    不過期）。AI 因此可能打空點——這是迷霧的本義，**不得**用 ground truth 回填修正。
    """
    views = IntelService(db).visible_contacts(session_id, faction, granularity)
    # contact_id → 真實 unit id（只在伺服端用；見上方 docstring）。
    unit_by_contact = {c.id: c.target_unit_id for c in store.query(db, session_id, faction)}
    out: list[dict[str, Any]] = []
    for v in views:
        enemy: dict[str, Any] = {
            "contact_id": v.contact_id,
            "lat": round(v.lat, 6),
            "lng": round(v.lng, 6),
            "fidelity": v.fidelity.value,
            "last_seen_tick": v.last_seen_tick,
            "error_radius_m": round(v.error_radius_m, 1),
        }
        unit_id = unit_by_contact.get(v.contact_id)
        if unit_id:
            enemy["unit_id"] = unit_id
        # 以下數欄由 fidelity 決定有無（IntelService 已閘門化）——None 就不放，讓 prompt 誠實留白。
        # ⚠ `echelon` 過去叫 `unit_type` 但裝的是階層；改名後這裡也要用對的字給 LLM，
        # 否則 prompt 裡會出現 `unit_type: PLATOON` 這種讓模型誤判兵種的鍵。
        if v.echelon:
            enemy["echelon"] = v.echelon
        if v.branch:
            enemy["branch"] = v.branch
        if v.designation:
            enemy["designation"] = v.designation
        if v.faction:
            enemy["faction"] = v.faction
        out.append(enemy)
    out.sort(key=lambda e: str(e.get("contact_id", "")))  # 決定性順序
    return out


def allied_units(
    db: Session,
    session_id: str,
    faction: str,
    relations: FactionRelations,
    snapshot: Mapping[str, UnitState] | None = None,
) -> list[dict[str, Any]]:
    """盟軍部隊（ALLIED 陣營）——走 units 共享視圖，不經偵測（#91 語義）。

    改版前盟軍對 AI 是**完全隱形**的：`build_faction_context` 的己方迴圈是嚴格 `faction ==`，
    而 `ground_truth_enemies` 只收 HOSTILE → 盟軍既不在 own_units 也不在 known_enemies。
    協同作戰的前提是看得到友軍，故補上此桶。

    `snapshot`（WP-C5，應為 `projected_snapshot` 的輸出）：有則位置以它為準——盟軍的位置同樣
    經該軍的回報鏈路而來，斷聯的盟軍在共享視圖上一樣是凍結的。**投影後沒有座標就是沒有**，
    不得退回 DB 的真實座標（那會讓凍結破功）。
    """
    units = db.scalars(select(TacticalUnit).where(TacticalUnit.session_id == session_id)).all()
    out: list[dict[str, Any]] = []
    for u in units:
        if u.faction == faction or not relations.is_allied(faction, u.faction):
            continue
        if u.current_strength is not None and float(u.current_strength) <= 0:
            continue  # 已殲滅的盟軍不列（共享視圖看得到其狀態，與敵情的「最後已知」不同）
        view: dict[str, Any] = {
            "unit_id": u.id,
            "faction": u.faction,
            "designation": u.designation,
            "unit_type": u.unit_level.value,
        }
        hot = snapshot.get(u.id) if snapshot is not None else None
        lat, lng = (
            (hot.get("lat"), hot.get("lng")) if hot is not None else (u.current_lat, u.current_lng)
        )
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            view["lat"] = round(float(lat), 6)
            view["lng"] = round(float(lng), 6)
        out.append(view)
    out.sort(key=lambda v: str(v["unit_id"]))
    return out


def _to_ledger_event(row: TacticalEventLog) -> LedgerEvent:
    """ORM row → LedgerEvent（`event_audience` 的入參型別；欄位同名，僅補 None 防護）。"""
    return LedgerEvent(
        event_type=row.event_type,
        tick=row.tick,
        initiator_id=row.initiator_id,
        target_id=row.target_id,
        ai_decision=dict(row.ai_decision or {}),
        damage_calc=row.damage_calc,
    )


def _event_summary(row: TacticalEventLog, ev: LedgerEvent) -> dict[str, Any]:
    """事件的精簡摘要（供 briefing）。只帶結果性欄位，不帶 hash/內部診斷。"""
    out: dict[str, Any] = {"tick": row.tick, "event_type": row.event_type}
    decision = ev.ai_decision
    for key in ("status", "reason", "target_health_after"):
        value = decision.get(key)
        if value is not None:
            out[key] = value
    # WP-C10.4：與 WS feed 共用同一條迷霧規則——面射擊沒有觀測就不給傷亡數字。
    # 只擋前端不擋這裡的話，LLM 指揮官會握有玩家沒有的完美戰果評估。
    damage = feed_damage(str(row.event_type), row.damage_calc)
    if damage is not None:
        out["damage"] = round(float(damage), 1)
    return out


def recent_events(
    db: Session,
    session_id: str,
    faction: str,
    faction_for: Any = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """該陣營**受眾可見**的最近 N 筆事件（時間正序）。

    受眾判定複用 `broadcaster.event_audience`（與 WS feed 同一套規則，含 SENSOR_CONTACT 的
    observer 優先陷阱）。`audience is None` ＝全域事件（收場/關係變更…），一律可見。

    排序用 `seq` 而非 tick/timestamp：rollback 後 tick 非單調，seq 才是帳本的時間軸身分。
    先多抓 `_EVENT_OVERSCAN` 倍再過濾截尾——否則最近 N 筆若全屬他方，本陣營會拿到空清單。
    """
    if limit <= 0 or faction_for is None:
        return []
    scan = min(limit * _EVENT_OVERSCAN, _EVENT_SCAN_CAP)
    rows = list(
        db.scalars(
            select(TacticalEventLog)
            .where(TacticalEventLog.session_id == session_id)
            .order_by(TacticalEventLog.seq.desc())
            .limit(scan)
        ).all()
    )
    visible: list[dict[str, Any]] = []
    for row in rows:  # seq 由新到舊
        if row.event_type in _EVENT_EXCLUDE:
            continue
        ev = _to_ledger_event(row)
        audience = event_audience(ev, faction_for)
        if audience is not None and faction not in audience:
            continue
        visible.append(_event_summary(row, ev))
        if len(visible) >= limit:
            break
    visible.reverse()  # 交給 LLM 時回到時間正序
    return visible
