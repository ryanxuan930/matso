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
        existing = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if existing is not None:
            existing.password_hash = hash_password(password)
            existing.role = role
            db.commit()
            print(f"✓ 更新種子使用者 {username}（{role.value}）")
            return
        db.add(User(username=username, password_hash=hash_password(password), role=role))
        db.commit()
        print(f"✓ 建立種子使用者 {username}（{role.value}）")


if __name__ == "__main__":
    main()
