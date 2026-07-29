"""Runner 重啟時把執行期狀態接回去（WP-E1；SPEC_V2 §6 WP-E1）。

`sim_runtime` 的 runner 會在多種情況下重建：core 重啟、崩潰、`session_restart_key`
（改自主 AI 指派）、rollback。**在此模組之前，每次重建都讓該局的 `SimClock` 回到 tick 0**
——Ledger 的 tick 倒退、`issued_at_tick` 歸零（指令排序壞）、comms 延遲閘門的 now_tick 倒退、
victory 的 time 條件永遠不到。本模組提供「續接點」的單一判定。

## 起始 tick 的來源優先序（且刻意不含 Ledger）

1. Redis `session:{id}:tick`——broadcaster 每個有活動的 tick 寫一次，且寫在該 tick 的
   Ledger 批次**提交之後**（kernel: append → publish_events → publish → checkpoint → advance）。
   core 崩潰但 Redis 存活時這是最新的。
2. 最近一次 checkpoint 的 tick（Redis 也沒了時）。
3. 都沒有 → 0（全新的局）。

兩者都代表「該 tick 已跑完」，故起始 tick ＝ 來源 + 1。

**為什麼不看 Ledger 的最大 tick**：rollback 後 tick 非單調（被棄世代的事件 tick 更大），
拿最大值會直接把白軍的回滾抵銷掉。單調的是 seq、不是 tick——這與 `checkpoint.py`
「ledgerSeq 才是時間軸身分」的既有結論一致。

**已知的重跑窗**：若崩潰發生在「Ledger 批次已寫、tick 鍵未更新」之間，該 tick 會重跑一次
（事件重複入帳，seq 續增）。已 drain 的指令狀態早已 commit 進 DB，不會被重跑第二次；
真正會重複的是移動步進。這是 at-least-once 的代價，比漏跑安全。
"""

from __future__ import annotations

import logging
from typing import Any

from app.state.checkpoint import CheckpointManager
from app.state.hot_state import session_tick_key

_LOG = logging.getLogger("app.resume")


def read_live_tick(client: Any, session_id: str) -> int | None:
    """讀 Redis 的當前 sim tick；無鍵/壞值 → None（與「tick 0」區別開來）。"""
    try:
        raw = client.get(session_tick_key(session_id))
    except Exception:  # Redis 掛掉不該擋住 runner 啟動
        return None
    if raw is None:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def resume_tick(session_factory: Any, client: Any, session_id: str) -> int:
    """runner (重)啟動時的起始 tick（見模組 docstring 的來源優先序）。"""
    live = read_live_tick(client, session_id)
    if live is not None:
        return live + 1
    record = CheckpointManager(session_factory).load_latest(session_id)
    if record is not None:
        _LOG.info("session %s 無 Redis tick，自 checkpoint tick=%d 續跑", session_id, record.tick)
        return record.tick + 1
    return 0
