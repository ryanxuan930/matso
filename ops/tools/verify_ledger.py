"""驗證某 Session 的 Event Ledger hash chain 完整性（SPEC_FULL §15.3）。

用法：
    uv run python ops/tools/verify_ledger.py --session <session_id>
    uv run python ops/tools/verify_ledger.py --session <id> --database-url mysql://...

Exit code 0 = 完整；1 = 偵測到斷點（缺號 / 鏈接錯 / 內容竄改）；2 = 使用錯誤。
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.config import Settings
from app.db import make_engine, make_session_factory
from app.models import TacticalEventLog
from app.state.ledger import verify_chain


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="驗證 Event Ledger hash chain 完整性")
    parser.add_argument("--session", required=True, help="WargameSession id")
    parser.add_argument(
        "--database-url",
        default=None,
        help="覆寫 DATABASE_URL（預設讀環境變數 / .env）",
    )
    args = parser.parse_args(argv)

    # URL 正規化統一走 Settings.sqlalchemy_url（O1.7/r13，勿在此複製 driver 映射邏輯）
    url = Settings(database_url=args.database_url).sqlalchemy_url if args.database_url else None
    engine = make_engine(url)
    session_factory = make_session_factory(engine)

    with session_factory() as db:
        stmt = (
            select(TacticalEventLog)
            .where(TacticalEventLog.session_id == args.session)
            .order_by(TacticalEventLog.seq.asc())
            .execution_options(yield_per=1000)  # streaming：大 session 不整批進記憶體（r15）
        )
        result = verify_chain(db.execute(stmt).scalars())

    if result.ok:
        print(f"OK：session {args.session} 的 {result.verified_count} 個事件 hash chain 完整。")
        return 0

    print(
        f"FAIL：session {args.session} 在 seq={result.break_seq} 偵測到斷點"
        f"（已驗證 {result.verified_count} 筆）。\n原因：{result.reason}"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
