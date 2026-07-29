"""一局的資料清除（WP-B1b）——DB 子表 + Redis 活狀態。

抽出來自成一個模組，是因為它有**兩個**呼叫端（`delete_session` 與演習的銷毀模式），
而在此之前它是 `delete_session` 裡的一份手寫清單——那份清單已經過期了：

> `Message` / `Request` / `FirePlan` / `FirePlanTarget` 都帶 `sessionId`，但 prisma 給它們的是
> **純 String 欄、沒有 FK**（`schema.prisma:263/282/450/468`）。所以刪 session 不會噴 FK 錯，
> 那些列就這樣**永遠孤兒化**。對「刪除推演」而言那是遺漏；對 WP-B1 的**銷毀模式**而言，
> 那是**資料殘留**——說好要銷毀的 C2 信文與火力計畫還躺在庫裡。

`_SESSION_TABLES` 因此改成**由模型自省導出**而不是手寫：任何未來新增的、帶 `session_id`
的表會自動入列。手寫清單的失效方式是安靜的，而安靜的失效正是這個 bug 的成因。
"""

from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.models.base import Base

# 刪除順序：**被別人參照的表最後刪**。
# `Order`/`IntelContact` 參照 unit → 必須早於 `TacticalUnit`；`TacticalUnit` 最後
# （它的 `EquipmentInstance` 與子單位由 DB 的 ondelete=CASCADE 帶走）。
# 其餘沒有互相參照，順序不影響。
_LAST: tuple[str, ...] = ("TacticalUnit",)
_LATE: tuple[str, ...] = ("Order", "IntelContact", "TacticalEventLog", "MapFeature")


def session_scoped_models() -> list[type[Base]]:
    """所有帶 `session_id` 欄的模型，依安全刪除序排好。

    自省而非手寫：新增一張 session 範圍的表時，**不需要**有人記得回來改這裡。

    ⚠ 走 SQLAlchemy 的 **mapper registry** 而不是 `app.models.__all__`。
    第一版寫的是後者，而 `Message`/`Request` **根本不在 `__all__` 裡**——
    那份自省會漏掉的，剛好就是本模組要修的那幾張表。
    「自省」若建在另一份手寫清單上，它只是把手寫清單換了個地方藏。
    """
    found = [
        mapper.class_ for mapper in Base.registry.mappers if hasattr(mapper.class_, "session_id")
    ]
    rank = {name: i for i, name in enumerate((*_LATE, *_LAST), start=1)}
    return sorted(found, key=lambda m: (rank.get(m.__name__, 0), m.__name__))


def purge_session_rows(db: Session, session_id: str) -> dict[str, int]:
    """清掉一局的所有 DB 列（含 session 本身）。回各表刪除筆數，供銷毀模式留痕。

    ⚠ 正式部署的應用帳號對 `TacticalEventLog` **沒有 DELETE 權**
    （`ops/tools/grant_ledger_readonly.sql`——帳本 append-only 是刻意的防線）。
    這個限制在既有的 `delete_session` 就已經存在；本函式不繞過它，
    真的要銷毀帳本得由 DBA 依 runbook 執行。開發用的 compose 跑 root，故本機不會踩到。
    """
    deleted: dict[str, int] = {}
    # FirePlanTarget 沒有 session_id（它掛在 planId 上），故先依 plan 反查刪掉。
    plan_ids = list(
        db.execute(select(models.FirePlan.id).where(models.FirePlan.session_id == session_id))
        .scalars()
        .all()
    )
    if plan_ids:
        res = db.execute(
            sa_delete(models.FirePlanTarget).where(models.FirePlanTarget.plan_id.in_(plan_ids))
        )
        deleted["FirePlanTarget"] = int(getattr(res, "rowcount", 0) or 0)

    for model in session_scoped_models():
        res = db.execute(sa_delete(model).where(model.session_id == session_id))  # type: ignore[attr-defined]
        deleted[model.__name__] = int(getattr(res, "rowcount", 0) or 0)

    session = db.get(models.WargameSession, session_id)
    if session is not None:
        db.delete(session)
        deleted["WargameSession"] = 1
    return deleted


def purge_session_redis(redis_url: str, session_id: str) -> int:
    """清掉一局在 Redis 的所有活狀態。回刪除的鍵數。

    整局的活狀態都在 `session:{id}:*`——熱狀態、廣播 ring/seq/channel、
    live_ammo/live_position/live_msel 命令通道、ai_config/ai_status。
    **在此之前刪除推演完全不清這些**：局沒了，Redis 裡的殘骸還在。

    以 `SCAN` 而非 `KEYS`：`KEYS` 在大庫上會阻塞整個 Redis。
    連不上 Redis 不視為失敗（該局可能根本沒跑過），由呼叫端決定要不要在意。
    """
    from app.cache import make_redis

    client = make_redis(redis_url)
    removed = 0
    for key in client.scan_iter(match=f"session:{session_id}:*", count=200):
        removed += int(client.delete(key) or 0)
    # ai_config / ai_status 走的是同一個 `session:{id}:` 前綴（見 ai_loop/orchestrator），
    # 所以上面的 scan 已經涵蓋。這裡不另外硬編鍵名——硬編的清單就是本模組要修掉的那種東西。
    return removed


__all__ = ["purge_session_redis", "purge_session_rows", "session_scoped_models"]
