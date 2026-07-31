"""契約與實作的路徑一致性——**全端點**（Backlog 清倉）。

## 為什麼需要這條

CI 只跑 `openapi_spec_validator`——它驗的是**規格語法**，不驗路由有沒有實作。
兩個方向的漂移都沒有任何閘門會發現：

- **契約有、實作沒有**＝規格殘骸。前端照著契約生型別、寫呼叫，然後在執行期吃 404。
- **實作有、契約沒有**＝前端拿不到型別，只能手刻 `any` 或猜欄位名。

`test_exercise_contract_conformance.py` 只釘了 `/exercises*`（那張卡的範圍）。
本檔擴到全部端點。

## 路徑參數名要正規化

契約寫 `/sessions/{id}`、實作寫 `/sessions/{session_id}`——**那不是漂移**，是命名不同。
不正規化的話會冒出 111 筆假陽性，把真正的 21 筆淹掉。比較的是**結構**不是參數名。

## 既有漂移列成清單，而不是把閘門關掉

裝這條閘門的當下就有 21 筆既有漂移。把它們列進允許清單有兩個好處：
1. **新的漂移立刻會紅**——這才是閘門的目的。
2. 既有的 21 筆變成**看得見的欠帳**，而不是沒人知道的事實。

⚠ 這份清單只能變短。要加東西進去，代表你正在製造新的漂移。
"""

from __future__ import annotations

import pathlib
import re

import yaml

from app.main import app

_CONTRACT = pathlib.Path(__file__).resolve().parents[3] / "contracts" / "core_api.yaml"
_PREFIX = "/api/v1"
_METHODS = {"get", "post", "patch", "delete", "put"}

# 不屬於版本化 API 的維運端點——不進契約是**對的**。
_NOT_PART_OF_THE_API = {
    ("GET", "/healthz"),  # 存活探針
    ("GET", "/metrics"),  # Prometheus 抓取（WP-E4，刻意 include_in_schema=False）
}

# ---- 既有漂移（裝閘門當下的實況）。**這份清單只能變短。** ----

# 契約有、實作沒有＝規格殘骸。前端照契約生型別然後在執行期吃 404。
_CONTRACT_ONLY = {
    ("GET", "/api/v1/admin/plugins"),
    ("POST", "/api/v1/admin/plugins/{}/toggle"),
    ("GET", "/api/v1/sessions/{}/aar"),  # 實作拆成 /aar/stats、/aar/report 等
    ("GET", "/api/v1/sessions/{}/ai/tasks/{}"),
    ("POST", "/api/v1/sessions/{}/ai/consult"),
    ("POST", "/api/v1/sessions/{}/injects"),  # 實作是單數 /inject
}

# 實作有、契約沒有＝前端拿不到型別。
_IMPL_ONLY = {
    ("GET", "/api/v1/sessions/{}/aar/export"),
    ("GET", "/api/v1/sessions/{}/aar/replay"),
    ("GET", "/api/v1/sessions/{}/aar/report"),
    # /aar/stats 已於 WP-D6.2 補進契約（新增 attempts / engagements_fired / stats_version 時）
    ("GET", "/api/v1/sessions/{}/autonomy"),
    ("PUT", "/api/v1/sessions/{}/autonomy"),
    ("DELETE", "/api/v1/sessions/{}/autonomy"),
    ("GET", "/api/v1/sessions/{}/orbat-permissions"),
    ("PUT", "/api/v1/sessions/{}/orbat-permissions"),
    ("POST", "/api/v1/sessions/{}/units/{}/reposition"),
    ("POST", "/api/v1/system/config/test-llm"),
}


def _norm(path: str) -> str:
    """路徑參數名正規化：`/sessions/{id}` 與 `/sessions/{session_id}` 是同一條路徑。"""
    return re.sub(r"\{[^}]+\}", "{}", path)


def _contract_ops() -> set[tuple[str, str]]:
    spec = yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))
    return {
        (method.upper(), _norm(f"{_PREFIX}{path}"))
        for path, item in spec["paths"].items()
        for method in item
        if method in _METHODS
    }


def _app_ops() -> set[tuple[str, str]]:
    """由 FastAPI 產生的 OpenAPI 取實作端點。

    刻意不走 `app.routes`：這個 FastAPI 版本把 include 進來的 router 包成 `_IncludedRouter`
    而不攤平，逐個 route 讀 `path` 會靜靜回空集合——那會讓斷言假綠。
    """
    return {
        (method.upper(), _norm(path))
        for path, item in app.openapi()["paths"].items()
        for method in item
        if method in _METHODS
    } - _NOT_PART_OF_THE_API


def test_no_new_spec_ghosts() -> None:
    """契約有、實作沒有的端點不可以增加。"""
    ghosts = _contract_ops() - _app_ops() - _CONTRACT_ONLY
    assert not ghosts, f"新的規格殘骸（契約有、實作沒有）：{sorted(ghosts)}"


def test_no_new_untyped_endpoints() -> None:
    """實作有、契約沒有的端點不可以增加——前端型別由契約生成。"""
    untyped = _app_ops() - _contract_ops() - _IMPL_ONLY
    assert not untyped, f"新的無契約端點（前端拿不到型別）：{sorted(untyped)}"


def test_the_known_drift_list_only_shrinks() -> None:
    """**清單只能變短**：修好一條就要從清單刪掉，否則閘門會慢慢失效。

    這條在「清單裡有、但實際上已經沒漂移了」時轉紅，逼人把它刪掉。
    """
    stale_ghosts = _CONTRACT_ONLY - (_contract_ops() - _app_ops())
    stale_untyped = _IMPL_ONLY - (_app_ops() - _contract_ops())
    assert not stale_ghosts, f"這些已經修好了，請從 _CONTRACT_ONLY 刪掉：{sorted(stale_ghosts)}"
    assert not stale_untyped, f"這些已經修好了，請從 _IMPL_ONLY 刪掉：{sorted(stale_untyped)}"


def test_the_gate_actually_sees_endpoints() -> None:
    """前提檢查：兩邊都要撈得到東西，否則上面三條全是假綠。"""
    assert len(_contract_ops()) > 40
    assert len(_app_ops()) > 40
