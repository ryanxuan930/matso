"""該局的機動規則（出貨預設 ⊕ 想定覆寫）——WP-B6。

放在獨立模組而非 `mobility_matrix.py`：後者是**零 DB 的純值層**（被 terrain 端鏡像、
被 golden 路徑間接依賴），加一個讀 DB 的函式會破壞那條界線。同 `orders/no_strike.py`
與 `orders/roe.py` 的「純解析 + 獨立載入層」分工。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.movement.mobility_matrix import MobilityRules, default_rules


def load_session_mobility_rules(db: Session, session_id: str) -> MobilityRules:
    """讀該局持久化的機動覆寫 → 疊在出貨預設上。未宣告 → 出貨預設本身（同一個物件）。"""
    from app.models import WargameSession

    row = db.get(WargameSession, session_id)
    patch = row.mobility_overrides if row is not None else None
    return default_rules().merged(patch)
