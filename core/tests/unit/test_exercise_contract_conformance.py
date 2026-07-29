"""契約與實作的路徑一致性（WP-B1）。

**為什麼需要這條**：CI 只跑 `openapi_spec_validator`——它驗的是規格語法，
**不驗路由有沒有實作**。既有的證據就在 repo 裡：`/sessions/{id}/ledger` 在契約裡躺了很久
卻從來沒有實作，而 `/aar/stats`、`/aar/report`、`/aar/export` 反過來是有實作沒契約。
兩個方向的漂移都沒有任何閘門會發現。

本檔只釘 `/exercises*`（本卡的範圍）。把它擴到全部端點會一次點亮那些既有漂移，
那屬於另一張卡（紅線 5），已記 PROGRESS Backlog。
"""

from __future__ import annotations

import pathlib

import yaml

from app.main import app

_CONTRACT = pathlib.Path(__file__).resolve().parents[3] / "contracts" / "core_api.yaml"
_PREFIX = "/api/v1"


def _contract_ops() -> set[tuple[str, str]]:
    spec = yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))
    return {
        (method.upper(), f"{_PREFIX}{path}")
        for path, item in spec["paths"].items()
        if path.startswith("/exercises")
        for method in item
        if method in {"get", "post", "patch", "delete", "put"}
    }


def _app_ops() -> set[tuple[str, str]]:
    """由 FastAPI 產生的 OpenAPI 取實作端點。

    刻意不走 `app.routes`：這個 FastAPI 版本把 include 進來的 router 包成 `_IncludedRouter`
    而不攤平，逐個 route 讀 `path` 會靜靜回空集合——那會讓本檔的兩條斷言雙雙假綠。
    產生的 schema 也才是 client 真正看得到的東西。
    """
    paths = app.openapi()["paths"]
    return {
        (method.upper(), path)
        for path, item in paths.items()
        if path.startswith(f"{_PREFIX}/exercises")
        for method in item
        if method in {"get", "post", "patch", "delete", "put"}
    }


def test_every_contract_exercise_path_is_implemented() -> None:
    missing = _contract_ops() - _app_ops()
    assert not missing, f"契約有、實作沒有（規格殘骸）：{sorted(missing)}"


def test_every_implemented_exercise_path_is_in_the_contract() -> None:
    """反方向同樣要擋：前端型別由契約生成，沒進契約的端點前端叫不到。"""
    extra = _app_ops() - _contract_ops()
    assert not extra, f"實作有、契約沒有（前端拿不到型別）：{sorted(extra)}"


def test_the_phantom_session_lifecycle_stub_is_gone() -> None:
    """`/sessions/{id}/lifecycle` 是未認證、無 schema、無實作的規格殘骸，本卡刪除。

    它長得就像演習階段機想要的端點——留著只會讓下一個人採用它並繼承那個洞。
    """
    spec = yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))
    assert "/sessions/{id}/lifecycle" not in spec["paths"]
