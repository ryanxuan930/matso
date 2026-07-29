"""at_tick 火力排程（WP-C10.3）——時間到了就把預劃目標打出去。

**為什麼不是 kernel 的 trigger 槽**（`TriggerChecker`）：

1. 那個介面回的是 `list[LedgerEvent]`，**產生不了令**（`scenario/triggers.py`）。
2. 它在 `run_tick` 的**最後**才跑，而令的 drain 在最前面——在那裡生的令一定慢一個 tick。
3. 活執行期根本沒接它（`sim_runtime` 傳的是 `NoOpTriggerChecker`）；順手接上去會把所有
   已載入想定的 MSEL 條目一起喚醒，那是本卡範圍外的行為變更（紅線 5）。

改走 `run_paced(pre_tick=…)`：它在 `kernel.run_tick()` **之前**跑，而 `run_tick` 的第一步
就是 drain——所以在 tick N 落庫的令會在**同一個 tick N** 被裁決，這正是「H-20 準備射擊」
要的準時語義。而且 `run_paced` 已經把 pre_tick 包在 try/except 裡，排程器出錯不會把
整個 runner 拖進重啟迴圈。

紅線 1：tick 一律來自呼叫端傳入的 `sim_clock`，本模組不看牆鐘、不抽隨機。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fires.service import fire_target, issuer_for
from app.models.enums import FirePlanStatus, FirePlanTargetStatus, FireSchedule
from app.models.tables import FirePlan, FirePlanTarget
from app.orders.service import OrderService

_LOG = logging.getLogger("app.fires.scheduler")


def due_targets(db: Session, session_id: str, tick: int) -> list[tuple[FirePlan, FirePlanTarget]]:
    """本 tick 該打的預劃目標，**順序確定**（計畫 → seq）。

    比較用 `at_tick <= tick` 而非 `== tick`：runner 會暫停、崩潰重啟、回滾，
    只認相等的話錯過的那一刻就**永遠不會補打**——而且不會有任何徵兆。
    寧可遲到也不要無聲消失；遲到看得出來（`fired_at_tick` 與 `at_tick` 的差）。
    """
    rows = db.execute(
        select(FirePlan, FirePlanTarget)
        .join(FirePlanTarget, FirePlanTarget.plan_id == FirePlan.id)
        .where(
            FirePlan.session_id == session_id,
            FirePlan.status == FirePlanStatus.ACTIVE,
            FirePlanTarget.schedule == FireSchedule.AT_TICK,
            FirePlanTarget.status == FirePlanTargetStatus.PENDING,
            FirePlanTarget.at_tick.is_not(None),
            FirePlanTarget.at_tick <= tick,
        )
        # 明確排序：沒有 ORDER BY 的話送令順序隨 DB 掃描順序變動，
        # 而令的 drain 排序是 (issued_at_tick, id)——同 tick 內就不可重播了。
        .order_by(FirePlan.created_at_tick, FirePlan.id, FirePlanTarget.seq, FirePlanTarget.id)
    ).all()
    return [(plan, target) for plan, target in rows]


def run_due_fire_missions(
    db: Session,
    session_id: str,
    tick: int,
    order_service_factory: Callable[[Session], OrderService],
) -> int:
    """把本 tick 到期的預劃目標逐一下令。回實際送出的道數。

    單一目標失敗（作者已離局／預檢不過／火協未核准）只影響該目標——`fire_target`
    自己會把它判 FAILED 並記原因。**一個目標打不出去不該讓整份計畫停擺。**
    """
    fired = 0
    for plan, target in due_targets(db, session_id, tick):
        issuer = issuer_for(db, plan)
        if issuer is None:
            target.status = FirePlanTargetStatus.FAILED
            target.failure_reason = "計畫建立者已不在本局，無人可作為下令者"
            db.commit()
            continue
        out = fire_target(
            db,
            plan,
            target,
            issuer_id=issuer,
            order_service_factory=order_service_factory,
            tick=tick,
        )
        if out.status is FirePlanTargetStatus.FIRED:
            fired += 1
        else:
            _LOG.info(
                "session %s 預劃目標 %s 未能執行：%s", session_id, target.id, out.failure_reason
            )
    return fired
