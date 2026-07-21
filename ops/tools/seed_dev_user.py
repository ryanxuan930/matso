"""建立開發/E2E 用的種子使用者（O4.1）。

用途：Playwright E2E 與本機手動測試需要一個可登入的帳號。
- **SQLite（throwaway dev/e2e DB）**：本腳本以 Base.metadata.create_all 建表（不違反「MariaDB
  只走 prisma migrate」紅線——那只適用正式 MariaDB）。
- **MariaDB**：假設表已由 prisma migrate 建好，本腳本只 upsert 使用者。

環境變數：
    DATABASE_URL        目標 DB（預設本機 compose MariaDB；E2E 設 sqlite:///./e2e.db）
    SEED_USERNAME       預設 commander
    SEED_PASSWORD       預設 exercise
    SEED_ROLE           預設 EXERCISE_DIRECTOR（可見全部 session）

用法：
    DATABASE_URL=sqlite:///./e2e.db uv run python ops/tools/seed_dev_user.py
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "core"))

from app.auth.hashing import hash_password
from app.config import Settings
from app.models import Base, User, UserRole


def main() -> None:
    settings = Settings()
    url = settings.sqlalchemy_url
    username = os.environ.get("SEED_USERNAME", "commander")
    password = os.environ.get("SEED_PASSWORD", "exercise")
    role = UserRole(os.environ.get("SEED_ROLE", "EXERCISE_DIRECTOR"))

    engine = create_engine(url, future=True)
    if url.startswith("sqlite"):
        Base.metadata.create_all(engine)  # throwaway dev/e2e DB 才建表

    with Session(engine) as db:
        user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user is not None:
            user.password_hash = hash_password(password)
            user.role = role
            print(f"✓ 更新種子使用者 {username}（{role.value}）")
        else:
            user = User(username=username, password_hash=hash_password(password), role=role)
            db.add(user)
            print(f"✓ 建立種子使用者 {username}（{role.value}）")
        db.commit()
        if os.environ.get("SEED_SESSION"):
            _seed_session(db, user)


def _seed_session(db: Session, user: User) -> None:
    """E2E 下令流程：固定 id 的 session + 藍軍單位 + 該使用者為藍方 COMMANDER 參與者。"""
    from app.models import SessionParticipant, TacticalUnit, UnitLevel, WargameSession

    sid = os.environ.get("SEED_SESSION_ID", "e2e-orders")
    if db.get(WargameSession, sid) is not None:
        print(f"✓ session {sid} 已存在，略過")
        return
    db.add(WargameSession(id=sid, name="E2E 下令演習", master_seed=42, current_weather={}))
    db.flush()
    for i in range(3):
        db.add(
            TacticalUnit(
                session_id=sid,
                designation=f"B{i + 1}",
                unit_level=UnitLevel.PLATOON,
                faction="BLUE",
                current_lat=23.75 + i * 0.02,
                current_lng=121.25 + i * 0.02,
            )
        )
    # 敵對陣營目標（RED）——供 ENGAGE E2E 選為 hostile 目標（ROE 允許，§12.1/O6.8）。
    db.add(
        TacticalUnit(
            session_id=sid,
            designation="R1",
            unit_level=UnitLevel.PLATOON,
            faction="RED",
            current_lat=23.80,
            current_lng=121.30,
        )
    )
    db.add(
        SessionParticipant(
            user_id=user.id,
            session_id=sid,
            faction="BLUE",
            role=UserRole.COMMANDER,
            unit_scope=[],
        )
    )
    db.commit()
    print(f"✓ 建立 E2E session {sid}（3 藍軍單位 + {user.username} 為 BLUE COMMANDER）")


if __name__ == "__main__":
    main()
