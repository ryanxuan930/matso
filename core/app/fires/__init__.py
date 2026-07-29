"""火力計畫（WP-C10.3）——預劃目標 + 排程。

**本套件不含任何物理**。一個預劃目標被「執行」的意思就是：組一份 `FireMissionPayload`
交給 `OrderService.submit`，其餘（驗證、物理預檢、火協 gate、席位）全部沿用既有那一條路。

紅線 3 的具體落實：這裡**沒有任何 bypass**。自動執行的令與人手按下去的令走同一個 `submit`，
被擋就是被擋——目標轉 `FAILED` 並記下原因，而不是繞過去。
"""

from app.fires.service import (
    FirePlanError,
    cancel_plan,
    create_plan,
    fire_target,
    list_plans,
)

__all__ = [
    "FirePlanError",
    "cancel_plan",
    "create_plan",
    "fire_target",
    "list_plans",
]
