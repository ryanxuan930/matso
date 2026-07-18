"""重新記錄 golden replay 的期望 stateHash（SPEC_FULL §19.1）。

何時用：**刻意**變更裁決/模擬邏輯（會改變確定性結果）後。重錄後 MUST 在 PR 說明變更原因。
非預期的 hash 變動代表可能引入了不該有的非決定性——不要盲目重錄。

用法：uv run python ops/tools/rerecord_golden.py

實作：以 MATSO_RERECORD_GOLDEN=1 執行 golden 測試，讓測試改為「寫入」而非「斷言」。
（本工具刻意不 import 測試碼，維持 ops 與測試解耦。）
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    env = {**os.environ, "MATSO_RERECORD_GOLDEN": "1"}
    result = subprocess.run(
        ["uv", "run", "pytest", "core/tests/replay", "-m", "golden", "-q"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
    )
    if result.returncode == 0:
        print("golden 已重新記錄至 core/tests/replay/goldens/；請檢視 diff 並在 PR 說明變更。")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
