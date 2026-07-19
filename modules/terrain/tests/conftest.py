"""terrain 測試 fixture：合成 DTED（session 內生成一次）與真檔路徑（realdata 用）。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from make_fixture import write_fixture


@pytest.fixture(scope="session")
def fixture_tiff(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_fixture(tmp_path_factory.mktemp("dted") / "fixture.tiff")


@pytest.fixture(scope="session")
def real_dted_path() -> Path:
    """真檔路徑（外接硬碟）。未設 MATSO_DTED_PATH 或檔案不存在 → skip（realdata 測試用）。"""
    raw = os.environ.get("MATSO_DTED_PATH")
    if not raw:
        pytest.skip("MATSO_DTED_PATH 未設定（真檔在外接硬碟時才跑 realdata 測試）")
    path = Path(raw)
    if not path.is_file():
        pytest.skip(f"MATSO_DTED_PATH 指向的檔案不存在（外接硬碟未掛載？）：{path}")
    return path
