"""簽證生效時的寫入閘門（WP-B4）。

放在**服務/端點邊界**而不是模型層：模型層擋不住 `seed_equipment` 那種內部寫入，
而那條路徑要的是「跳過」而不是「拋錯」（見 `adjudication/seed_equipment`）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.errors import ParamsSealedError
from app.governance.seal import active_seal, build_seal_payload, compute_seal_hash, seal_for


def require_params_unsealed(db: Session) -> None:
    """有任何演習的簽證生效中 → 403 PARAMS_SEALED，訊息指名是哪一場。

    **指名很重要**：操作員看到的若只是 `PARAMS_SEALED`，他不知道要去找誰解鎖。
    """
    found = active_seal(db)
    if found is None:
        return
    seal, exercise = found
    raise ParamsSealedError(
        f"參數已於演習「{exercise.name}」簽證鎖定（{exercise.phase.value}），"
        f"演習期間不得修改全域參數",
        details={
            "exercise_id": exercise.id,
            "exercise_name": exercise.name,
            "phase": exercise.phase.value,
            "content_hash": seal.content_hash,
        },
    )


def params_sealed(db: Session) -> bool:
    """唯讀查詢（供 seed 路徑決定要不要跳過寫入）。"""
    return active_seal(db) is not None


def seal_violation(db: Session, session_id: str) -> str | None:
    """開局前的簽證比對（WP-B4）。回不符的說明，或 None（可起）。

    **沒有 session start 端點可掛守衛**：`SimManager` 每 3 秒掃一次 DB，把每個非封存的
    session 都跑起來——建列即開跑。所以這是 `_ensure` 裡的一個早退，形狀與既有的
    `session_concluded_key` 相同。也因為掃描永遠重試，呼叫端**不可每輪落一次事件**，
    否則會灌爆帳本。

    **未掛演習的散局一律回 None**——驗收條文的「散局不受影響」講的正是這件事。
    """
    from app.models import WargameSession

    session = db.get(WargameSession, session_id)
    if session is None or not session.exercise_id:
        return None
    seal = seal_for(db, session.exercise_id)
    if seal is None:
        return None
    current = compute_seal_hash(build_seal_payload(db))
    if current == seal.content_hash:
        return None
    return (
        f"參數簽證不符：簽證於 {seal.content_hash[:12]}…，目前為 {current[:12]}…"
        f"（演習 {session.exercise_id}）"
    )


__all__ = ["params_sealed", "require_params_unsealed", "seal_violation"]
