"""勝負判定綁定 — O11.5（SPEC_AUTONOMY §5）。

複用 `scenario/triggers.py` 的條件 DSL（evaluate_condition / check_victory）。本模組補：
1. 從**活熱狀態**組 TriggerContext（各陣營戰力和 + 單位位置）。
2. 預設「最後存活陣營」勝負條件（AI-vs-AI 常態）+ 可選時限。
3. 一條 async **勝負監視器**：週期評估 → 有勝方即 `on_conclude` → 收場（emit SESSION_CONCLUDED
   + 停 runner，於 sim_runtime 接線）。

紅線：勝負由**確定性 DSL 對物理狀態**求值，**非** LLM 裁定。
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.ai_loop.worker import load_unit_meta
from app.scenario.triggers import TriggerContext, check_victory
from app.state.hot_state import HotStateStore

_LOG = logging.getLogger("app.victory")

_DEFAULT_POLL_S = 5.0
_STOP_POLL_S = 1.0


def build_trigger_context(
    hot_snapshot: dict[str, dict[str, Any]],
    unit_meta: dict[str, Any],
    tick: int,
) -> TriggerContext:
    """熱狀態快照 + 單位身分 → TriggerContext（陣營戰力和、單位位置）。"""
    strength: dict[str, float] = defaultdict(float)
    positions: list[tuple[str, float, float]] = []
    for uid, state in hot_snapshot.items():
        meta = unit_meta.get(uid)
        if meta is None:
            continue
        raw = state.get("strength")
        strength[meta.faction] += float(raw) if isinstance(raw, int | float) else 0.0
        lat, lng = state.get("lat"), state.get("lng")
        if isinstance(lat, int | float) and isinstance(lng, int | float):
            positions.append((meta.faction, float(lat), float(lng)))
    # 確保所有宣告陣營都在戰力表（即使全滅＝0），供 faction_eliminated 正確判定。
    for meta in unit_meta.values():
        strength.setdefault(meta.faction, 0.0)
    return TriggerContext(tick=tick, faction_strength=dict(strength), unit_positions=positions)


def last_standing_conditions(factions: list[str]) -> list[dict[str, Any]]:
    """預設勝負：某陣營在**所有其他陣營戰力歸零**時獲勝（最後存活）。"""
    conds: list[dict[str, Any]] = []
    for f in factions:
        others = [x for x in factions if x != f]
        if not others:
            continue
        conds.append(
            {
                "faction": f,
                "condition": {
                    "type": "all",
                    "of": [{"type": "faction_eliminated", "faction": o} for o in others],
                },
            }
        )
    return conds


def resolve_victory_conditions(
    explicit: list[dict[str, Any]] | None, factions: list[str]
) -> list[dict[str, Any]]:
    """優先用場景/指派的顯式條件；否則對現有陣營套用「最後存活」預設。"""
    if explicit:
        return explicit
    return last_standing_conditions(sorted(set(factions)))


async def _sleep_or_stop(seconds: float, should_stop: Callable[[], bool]) -> None:
    waited = 0.0
    while waited < seconds and not should_stop():
        await asyncio.sleep(min(_STOP_POLL_S, seconds - waited))
        waited += _STOP_POLL_S


async def run_victory_monitor(
    *,
    session_id: str,
    hot: HotStateStore,
    db_factory: Callable[[], Session],
    victory_conditions: list[dict[str, Any]],
    on_conclude: Callable[[list[str], int], None],
    should_stop: Callable[[], bool],
    tick_source: Callable[[], int] = lambda: 0,
    poll_s: float = _DEFAULT_POLL_S,
) -> None:
    """週期評估勝負；有勝方 → 呼叫 on_conclude(winners, tick) 後結束（收場由 on_conclude 接線）。"""
    if not victory_conditions:
        return
    _LOG.info("勝負監視器啟動：session=%s 條件數=%d", session_id, len(victory_conditions))
    while not should_stop():
        try:
            winners = await asyncio.to_thread(
                _evaluate, session_id, hot, db_factory, victory_conditions, tick_source()
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.exception("勝負評估失敗：session=%s", session_id)
            winners = []
        if winners:
            tick = tick_source()
            _LOG.info("session %s 勝負底定：winners=%s @tick %d", session_id, winners, tick)
            on_conclude(winners, tick)
            return
        await _sleep_or_stop(poll_s, should_stop)


def _evaluate(
    session_id: str,
    hot: HotStateStore,
    db_factory: Callable[[], Session],
    victory_conditions: list[dict[str, Any]],
    tick: int,
) -> list[str]:
    snapshot = hot.get_all()
    db = db_factory()
    try:
        unit_meta = load_unit_meta(db, session_id)
    finally:
        db.close()
    ctx = build_trigger_context(snapshot, unit_meta, tick)
    return check_victory(victory_conditions, ctx)
