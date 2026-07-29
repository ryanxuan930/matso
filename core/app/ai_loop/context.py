"""Faction-scoped COP context builder — O11.1（SPEC_AUTONOMY §3.2）。

把**單一陣營**的戰場視角組成緊湊、可序列化的 dict + 文字 briefing，供 LLM 指揮官 prompt。
純讀、零 I/O：呼叫端傳入快照（熱狀態拷貝、單位靜態身分、已霧化的敵情、關係矩陣、目標、
近期事件），本模組只投影與塑形。

紅線（fog of war，SPEC_AUTONOMY §1.4）：霧化在 `known_enemies` 的**注入端**強制——呼叫端 MUST
已依陣營過濾（真偵測走 IntelService；感測 NoOp 期間走 ground-truth HOSTILE）。本模組**永不**放寬
可見性，也不從己方視角推導未給定的敵情。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import h3

from app.adjudication import suppression as _sup
from app.factions.relations import FactionRelations
from app.state.hot_state import UnitState

# 目標/hex 匹配解析度（與交戰天氣同級 res 8）。座標→h3 供 SEIZE_HEX 等目標比對。
_OBJECTIVE_H3_RES = 8
_DEGRADED_HEALTH = 50.0  # health（效能%）低於此判 DEGRADED


@dataclass(frozen=True, slots=True)
class UnitMeta:
    """單位靜態身分（來自 DB TacticalUnit；熱狀態不存這些）。"""

    faction: str
    designation: str
    unit_type: str
    is_fixed: bool = False  # 固定單位（指揮部等）：不可移動，AI 不應派其機動/交戰。
    mobility_profile: str = "FOOT"  # #80：由編裝導出（FOOT/WHEELED/TRACKED）。
    speed_kmh: float | None = None  # #80：越野速度（km/h）；供 AI 判斷單回合可達距離。
    range_km: float | None = None  # #84：現有油量還能走的公里數（None＝徒步不受限）。


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def unit_status(state: UnitState) -> str:
    """由熱狀態導出簡易戰備狀態（無獨立 status 欄，以 strength/health 導出）。"""
    strength = _num(state.get("strength"))
    if strength is not None and strength <= 0:
        return "DESTROYED"
    health = _num(state.get("health"))
    if health is not None and health < _DEGRADED_HEALTH:
        return "DEGRADED"
    return "OPERATIONAL"


def _own_unit_view(unit_id: str, state: UnitState, meta: UnitMeta) -> dict[str, Any]:
    """己方單位視圖：完整揭露（自己的部隊）。"""
    view: dict[str, Any] = {
        "unit_id": unit_id,
        "designation": meta.designation,
        "type": meta.unit_type,
        "status": unit_status(state),
    }
    if meta.is_fixed:
        view["fixed"] = True  # 固定單位：AI 不應派其移動/機動交戰（MOVE 令會被驗證層擋下）。
    else:
        view["mobility"] = meta.mobility_profile  # #80：機動能力（步兵慢、機械化快）。
        if meta.speed_kmh is not None:
            view["speed_kmh"] = round(meta.speed_kmh, 1)
        if meta.range_km is not None:
            view["range_km"] = round(meta.range_km, 1)  # #84：油料剩餘行程
    lat, lng = _num(state.get("lat")), _num(state.get("lng"))
    if lat is not None and lng is not None:
        view["lat"] = round(lat, 6)
        view["lng"] = round(lng, 6)
        view["h3"] = h3.latlng_to_cell(lat, lng, _OBJECTIVE_H3_RES)
    # WP-C5 通聯後果：斷聯的單位**收不到新令**（`order_admissible`），且其位置只是最後一次
    # 回報。不告訴 LLM 的話，它會對一支聽不到命令的部隊反覆下令，還以為位置是即時的。
    comms = state.get("comms_state")
    if isinstance(comms, str) and comms and comms != "ONLINE":
        view["comms"] = comms
    stale = state.get("stale_since_tick")
    if isinstance(stale, int) and not isinstance(stale, bool):
        view["stale_since_tick"] = stale
    for key in ("strength", "health"):
        num = _num(state.get(key))
        if num is not None:
            view[key] = round(num, 1)
    # WP-C1 壓制與姿態。**只在非中性值時出現**（同 `comms` 的作法）：既有局的 prompt 位元不變，
    # 於是 ReplayClient 的 prompt 雜湊不動、既有 golden 自主場次不必重錄。
    #
    # 敵方的壓制度不在此揭露——`known_enemies` 走情報路徑，那裡本來就沒有這個欄位。
    sup = _num(state.get("suppression"))
    if sup is not None and sup > 0:
        view["suppression"] = round(sup, 2)
    posture = state.get("posture")
    if isinstance(posture, str) and posture and posture != "MOVING":
        view["posture"] = posture
    ammo = state.get("ammo_by_weapon")
    if isinstance(ammo, dict):
        view["ammo_by_weapon"] = {
            str(k): int(v) for k, v in ammo.items() if isinstance(v, int | float)
        }
    return view


def build_faction_context(
    *,
    faction: str,
    tick: int,
    hot_snapshot: dict[str, UnitState],
    unit_meta: dict[str, UnitMeta],
    known_enemies: list[dict[str, Any]],
    relations: FactionRelations,
    allied_units: list[dict[str, Any]] | None = None,
    objectives: list[dict[str, Any]] | None = None,
    recent_events: list[dict[str, Any]] | None = None,
    mission: str = "",
) -> dict[str, Any]:
    """組出 `faction` 視角的 COP context dict。

    - `hot_snapshot`：hot.get_all() 的拷貝（uid→state）。只投影 `unit_meta` 認得的單位。
    - `unit_meta`：uid→UnitMeta（DB 靜態身分）；決定 faction 分流（熱狀態無 faction）。
    - `known_enemies`：**已霧化**的敵情清單（呼叫端保證只含本陣營可見者）。原樣帶入。
    - `relations`：對稱關係矩陣；輸出本陣營對各宣告陣營的關係。
    - `allied_units`：盟軍部隊（走 units 共享視圖，非偵測；WP-A1）。原樣帶入。
    - `objectives` / `recent_events` / `mission`：態勢與意圖，原樣帶入（可序列化）。

    紅線：本函式不讀 DB/Redis、不放寬可見性；敵情只來自 `known_enemies`。
    """
    own: list[dict[str, Any]] = []
    for uid, state in hot_snapshot.items():
        meta = unit_meta.get(uid)
        if meta is None or meta.faction != faction:
            continue
        own.append(_own_unit_view(uid, state, meta))
    own.sort(key=lambda u: u["unit_id"])

    # 關係由**宣告陣營**（unit_meta，劇本層知識）導出，非由存活單位——避免洩漏敵方存活情形。
    other_factions = sorted({m.faction for m in unit_meta.values() if m.faction != faction})
    rel = {f: relations.relation(faction, f).value for f in other_factions}

    return {
        "faction": faction,
        "tick": tick,
        "mission": mission,
        "own_units": own,
        "allied_units": list(allied_units or []),
        "known_enemies": list(known_enemies),
        "relations": rel,
        "objectives": list(objectives or []),
        "recent_events": list(recent_events or []),
    }


def _comms_note(u: dict[str, Any]) -> str:
    """通聯後果的敘述（WP-C5）。ONLINE 不出現——只有反常才值得佔 prompt 篇幅。"""
    comms = u.get("comms")
    if not comms:
        return ""
    stale = u.get("stale_since_tick")
    when = f"，位置為 tick {stale} 的最後回報" if stale is not None else "，位置不明"
    tail = "新令無法送達" if comms == "OFFLINE" else "新令延遲送達"
    return f"｜【通聯 {comms}：{tail}{when}】"


def _posture_note(u: dict[str, Any]) -> str:
    """壓制與姿態的敘述（WP-C1）。中性值不出現——只有反常才值得佔 prompt 篇幅（同 `_comms_note`）。

    **壓制要講後果而不只是數字**：「0.7」對 LLM 沒有意義，「射擊效能剩三成、停火約 2 分鐘鬆動」
    才推得出「先撤出被壓制區或先反砲兵」這種決策。
    """
    parts = []
    sup = u.get("suppression")
    if isinstance(sup, int | float) and sup > 0:
        pct = round((1.0 - _sup.SUPPRESSION_FIRE_PENALTY * float(sup)) * 100)
        parts.append(f"【壓制 {sup}：射擊效能剩約 {pct}%、移動變慢；停火後每分鐘衰減】")
    posture = u.get("posture")
    if isinstance(posture, str) and posture and posture != "MOVING":
        mod = _sup.POSTURE_MODIFIER.get(_sup.Posture(posture), 1.0)
        parts.append(f"【姿態 {posture}：被命中率 ×{mod}；一旦移動即作廢，需重新構工】")
    return "｜" + "".join(parts) if parts else ""


def _fmt_own(u: dict[str, Any]) -> str:
    pos = f"({u['lat']:.4f},{u['lng']:.4f})" if "lat" in u else "位置未知"
    ammo = u.get("ammo_by_weapon") or {}
    ammo_s = "、".join(f"{k}×{v}" for k, v in ammo.items()) or "—"
    # 固定單位（指揮部等）：明確標註「勿調動」——不可下 MOVE，勿派其機動或投入攻勢交戰。
    fixed = "【固定·勿調動】" if u.get("fixed") else ""
    # #80 機動：標示速度供 LLM 判斷「這回合走得到哪」；步兵慢、機械化快。
    mob = ""
    if not u.get("fixed") and u.get("speed_kmh") is not None:
        mob = f"｜機動：{u.get('mobility', '?')} {u['speed_kmh']}km/h"
        # #84：有油料限制者標剩餘行程 → LLM 不會下超出油料的長程移動。
        if u.get("range_km") is not None:
            mob += f"（剩餘行程 {u['range_km']}km）"
    return (
        f"- {u['unit_id']}（{u.get('designation', '?')}｜{u.get('type', '?')}）{fixed}"
        f" {u.get('status', '?')} 戰力{u.get('strength', '?')} @ {pos}{mob}｜彈藥：{ammo_s}"
        f"{_posture_note(u)}{_comms_note(u)}"
    )


def _fmt_ally(u: dict[str, Any]) -> str:
    pos = f"({u['lat']:.4f},{u['lng']:.4f})" if "lat" in u else "位置未知"
    return (
        f"- {u['unit_id']}（{u.get('designation', '?')}｜{u.get('unit_type', '?')}"
        f"｜{u.get('faction', '?')}）@ {pos}"
    )


def _fmt_enemy(e: dict[str, Any]) -> str:
    """一則敵情。

    識別碼優先用 `unit_id`：ENGAGE 令要以真實單位 id 指定目標（見 world_view.contacts_from_intel），
    給 contact_id 會讓 LLM 產出橋接不了的令。**時間戳與誤差半徑必須渲染**——WP-A1 要求 DETECTED
    級只給「概略位置與時間戳」，少了它們 LLM 無從分辨「剛剛看到」與「兩百 tick 前看到」，
    也就學不會情報會過時。
    """
    ident = e.get("unit_id") or e.get("contact_id") or "未知接觸"
    lat, lng = _num(e.get("lat")), _num(e.get("lng"))
    pos = f"({lat:.4f},{lng:.4f})" if lat is not None and lng is not None else "位置不明"
    extras = [str(e[k]) for k in ("faction", "designation", "unit_type", "type") if e.get(k)]
    tail = f"｜{' '.join(extras)}" if extras else ""
    seen = ""
    if e.get("last_seen_tick") is not None:
        seen = f"｜最後觀測 tick {e['last_seen_tick']}"
        err = _num(e.get("error_radius_m"))
        if err is not None:
            seen += f"（誤差 ±{err:.0f}m）"
    fidelity = f"｜{e['fidelity']}" if e.get("fidelity") else ""
    return f"- {ident} @ {pos}{tail}{fidelity}{seen}"


def _fmt_event(ev: dict[str, Any]) -> str:
    """一則近期事件。非 dict（既有測試/呼叫端傳字串）則原樣輸出，維持相容。"""
    if not isinstance(ev, dict):
        return f"- {ev}"
    parts = [f"tick {ev['tick']}" if ev.get("tick") is not None else None, ev.get("event_type")]
    for key in ("status", "reason"):
        if ev.get(key):
            parts.append(str(ev[key]))
    if ev.get("damage") is not None:
        parts.append(f"傷害 {ev['damage']}")
    if ev.get("target_health_after") is not None:
        parts.append(f"目標剩餘 {ev['target_health_after']}")
    return "- " + "｜".join(p for p in parts if p)


def render_context_prompt(ctx: dict[str, Any]) -> str:
    """把 context dict 渲染為緊湊中文 briefing（LLM user prompt 的態勢部分）。

    僅渲染態勢；輸出格式指示（要 LLM 回什麼 schema）由 decider 的 system prompt 負責（P-B）。
    """
    lines = [f"# 戰場態勢（模擬 tick {ctx.get('tick', 0)}）｜你指揮陣營：{ctx.get('faction', '?')}"]
    if ctx.get("mission"):
        lines.append(f"## 本陣營任務目標\n{ctx['mission']}")

    rel = ctx.get("relations") or {}
    if rel:
        lines.append("## 陣營關係\n" + "、".join(f"{f}：{r}" for f, r in rel.items()))

    own = ctx.get("own_units") or []
    lines.append(f"## 我方部隊（{len(own)}）")
    lines.extend(_fmt_own(u) for u in own) if own else lines.append("- （無存活單位）")

    allies = ctx.get("allied_units") or []
    if allies:
        lines.append(f"## 盟軍部隊（{len(allies)}；經共享視圖，非本軍偵測）")
        lines.extend(_fmt_ally(u) for u in allies)

    enemies = ctx.get("known_enemies") or []
    lines.append(f"## 已知敵情（{len(enemies)}；僅列偵測所及，未偵測者不在此）")
    if enemies:
        lines.extend(_fmt_enemy(e) for e in enemies)
    else:
        lines.append("- （目前無敵情接觸）")

    objectives = ctx.get("objectives") or []
    if objectives:
        lines.append("## 目標/勝負條件")
        lines.extend(f"- {o}" for o in objectives)

    events = ctx.get("recent_events") or []
    if events:
        lines.append("## 近期關鍵事件")
        lines.extend(_fmt_event(ev) for ev in events)

    return "\n".join(lines)
