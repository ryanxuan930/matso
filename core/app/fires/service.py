"""火力計畫服務（WP-C10.3）——建立、查詢、取消，以及「把一個預劃目標打出去」。

`fire_target` 是本模組的核心，也是唯一會產生副作用的函式。它刻意做得很薄：

    預劃目標 → FireMissionPayload → OrderService.submit

**不繞過任何閘門**（紅線 3）。自動排程與人手呼叫共用這一個函式，所以兩條路徑不可能
在權限上分岔——「排程走的那條忘了套 gate」是這種功能最典型的洞。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import MatsoError
from app.models.enums import FirePlanStatus, FirePlanTargetStatus, FireSchedule
from app.models.tables import FirePlan, FirePlanTarget, SessionParticipant
from app.orders.schemas import OrderRequest, OrderType
from app.orders.service import OrderService


class FirePlanError(MatsoError):
    """火力計畫的操作錯誤（不存在／非本陣營／已執行過）。"""

    error_code = "FIRE_PLAN_INVALID"
    http_status = 409


@dataclass(frozen=True, slots=True)
class NewTarget:
    """建立計畫時的一個預劃目標（API 層已驗過型別）。"""

    target_lat: float
    target_lng: float
    shooter_unit_id: str
    label: str | None = None
    rounds: int = 4
    schedule: FireSchedule = FireSchedule.ON_CALL
    at_tick: int | None = None
    fire_request_id: str | None = None


def create_plan(
    db: Session,
    session_id: str,
    faction: str,
    name: str,
    targets: list[NewTarget],
    *,
    participant_id: str | None,
    tick: int,
) -> FirePlan:
    """建立火力計畫。`seq` 由清單順序決定——**排程器依 seq 執行，順序必須確定**。"""
    plan = FirePlan(
        session_id=session_id,
        faction=faction,
        name=name,
        status=FirePlanStatus.ACTIVE,
        created_by_participant_id=participant_id,
        created_at_tick=tick,
    )
    db.add(plan)
    db.flush()
    for seq, t in enumerate(targets):
        db.add(
            FirePlanTarget(
                plan_id=plan.id,
                seq=seq,
                label=t.label,
                target_lat=t.target_lat,
                target_lng=t.target_lng,
                rounds=t.rounds,
                shooter_unit_id=t.shooter_unit_id,
                schedule=t.schedule,
                at_tick=t.at_tick,
                fire_request_id=t.fire_request_id,
                status=FirePlanTargetStatus.PENDING,
            )
        )
    db.commit()
    return plan


def list_plans(db: Session, session_id: str, faction: str, omniscient: bool) -> list[FirePlan]:
    """本陣營的計畫（全知見全部）。**過濾在這裡做**——火力計畫是陣營私有情報（紅線 3）。"""
    stmt = select(FirePlan).where(FirePlan.session_id == session_id)
    if not omniscient:
        stmt = stmt.where(FirePlan.faction == faction)
    return list(db.scalars(stmt.order_by(FirePlan.created_at_tick, FirePlan.id)).all())


def targets_of(db: Session, plan_id: str) -> list[FirePlanTarget]:
    return list(
        db.scalars(
            select(FirePlanTarget)
            .where(FirePlanTarget.plan_id == plan_id)
            .order_by(FirePlanTarget.seq, FirePlanTarget.id)
        ).all()
    )


def cancel_plan(db: Session, plan: FirePlan) -> None:
    """取消計畫：未執行的目標轉 SKIPPED。

    **已執行的目標不動**——那些令已經落在帳本上了，改這裡的狀態不會讓砲彈飛回去。
    """
    plan.status = FirePlanStatus.CANCELLED
    for t in targets_of(db, plan.id):
        if t.status is FirePlanTargetStatus.PENDING:
            t.status = FirePlanTargetStatus.SKIPPED
    db.commit()


def fire_target(
    db: Session,
    plan: FirePlan,
    target: FirePlanTarget,
    *,
    issuer_id: str,
    order_service_factory: Callable[[Session], OrderService],
    tick: int,
) -> FirePlanTarget:
    """把一個預劃目標打出去——組令 → `OrderService.submit`。

    **一個預劃目標只打一次**：非 PENDING 一律拒。重複執行不是「多打幾發」而是計畫被
    執行了兩遍，那是排程器出錯的徵兆，應該擋在這裡而不是靜靜地再打一輪。

    下令被擋（權限/預檢/火協）→ 目標轉 `FAILED` 並記下原因，**不往上拋**：
    排程器在 tick 之間跑，一個目標打不出去不該讓整個排程器停擺。
    """
    if target.status is not FirePlanTargetStatus.PENDING:
        raise FirePlanError(f"此預劃目標已非待命狀態（目前 {target.status.value}）")

    payload: dict[str, object] = {
        "target_lat": target.target_lat,
        "target_lng": target.target_lng,
        "rounds": target.rounds,
        # **這個鍵有兩個用途**，都不能省：
        # (1) AAR 追溯——這一道令是哪個預劃目標打出去的。
        # (2) 繞開去重：`OrderService._find_active_duplicate` 比對的是 payload 原始 dict，
        #     同一門砲對同一座標同樣發數的兩個預劃目標會被判為重複而**只打一發**
        #     （而且回 200 假裝成功）。帶上目標 id 才會是兩道令。
        "fire_plan_target_id": target.id,
    }
    if target.fire_request_id:
        payload["fire_request_id"] = target.fire_request_id

    service = order_service_factory(db)
    try:
        resp = service.submit(
            plan.session_id,
            OrderRequest(
                unit_id=target.shooter_unit_id,
                order_type=OrderType.FIRE_MISSION,
                payload=payload,
            ),
            issuer_id,
        )
    except MatsoError as err:
        target.status = FirePlanTargetStatus.FAILED
        target.failure_reason = f"{err.error_code}：{err}"
        db.commit()
        return target

    target.status = FirePlanTargetStatus.FIRED
    target.order_id = resp.id
    target.fired_at_tick = tick
    target.failure_reason = None
    db.commit()
    return target


def issuer_for(db: Session, plan: FirePlan) -> str | None:
    """自動執行時要用誰的身分下令——**計畫的建立者**。

    沒有「系統」這個下令者：`validate_order` 要求 issuer 解析得到本局的 `SessionParticipant`。
    用建立者而非另造一個假帳號，是因為預劃火力的當責者本來就是寫這份計畫的人，
    而且陣營／指揮範圍／席位的檢查都因此維持真實（假帳號的 seat_role 是 NULL，
    等於把那些檢查一起繞掉了）。

    建立者已被移出本局 → 回 None，呼叫端把該目標判 FAILED。
    """
    pid = plan.created_by_participant_id
    if not pid:
        return None
    part = db.get(SessionParticipant, pid)
    return part.id if part is not None else None
