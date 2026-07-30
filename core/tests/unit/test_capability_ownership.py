"""「這個能力現在由誰負責」的漂移守門（低優先清理批次 L3/L4）。

盤點列了五個「重複實作的死函式」。逐一查證後**沒有一個刪得掉**：每一個都被一支專屬測試
釘著某條語義，而測試檔不在這一批的可改清單裡。於是改成在原處寫清楚兩件事——
「它為什麼沒有呼叫端」與「這個能力現在由誰負責」。

那些註解是會過期的敘述，所以這裡替它們加證據：

- 若哪天有人把死函式接回生產路徑 → 「零呼叫端」那段就是謊，本檔轉紅逼他改掉註解。
- 若哪天接班的實作被拔掉 → 「現在由誰負責」那段就是謊，本檔一樣轉紅。

這正是 repo 反覆出事的病（建好但零呼叫端、同一份能力兩處實作），差別只在這次是
**刻意保留 + 標記**，不是忘了接線；沒有標記的話下一輪盤點會再把它們當成新發現重挖一次。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_APP = Path(__file__).resolve().parents[3] / "core" / "app"


def _sources() -> list[tuple[Path, str]]:
    return [(p, p.read_text(encoding="utf-8")) for p in sorted(_APP.rglob("*.py"))]


def _call_sites(symbol: str) -> set[str]:
    """`core/app` 底下實際**呼叫**該符號的模組（相對路徑）。

    刻意用 AST 而非文字搜尋：`app/comms/__init__.py` 之類的 re-export 會 import 並列進
    `__all__`，那是「還掛在公開介面上」而不是「有人在用」——兩者混為一談，
    這個守門就永遠是綠的。
    """
    hits: set[str] = set()
    for path, src in _sources():
        for node in ast.walk(ast.parse(src, filename=str(path))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name == symbol:
                hits.add(str(path.relative_to(_APP)))
    return hits


# ── L3：五個死函式仍是零呼叫端（註解如此宣稱） ────────────────────────────

# 符號 → 註解裡寫的接班人（一併在下面驗它還活著）。
_RETIRED: dict[str, str] = {
    "fratricide_victims": "area_fire 直接寫 friendly_losses，fire_wiring 讀它",
    "reconstruct_states": "state_frames（/aar/replay/states 用逐 tick 差異）",
    "can_receive_command": "order_admissible（同時處理 OFFLINE 拒收與 DEGRADED 延遲）",
    "position_report_frozen": "project_position（回要用哪個座標，不只回布林）",
    "TerrainClientPlanner": "engine/movement.UnitMovementSystem + movement/router.plan_route",
}


@pytest.mark.parametrize("symbol", sorted(_RETIRED))
def test_retired_helper_still_has_no_production_call_site(symbol: str) -> None:
    """抓的病：這五個函式的 docstring 明寫「生產環境零呼叫端」並指名接班人。

    那種敘述最容易變成謊——`api/system.py` 的模組說明就是這樣停在「AI 決策迴路尚未接入」
    整整兩個里程碑（同批次 L4）。有人把其中一個接回生產路徑時，本條轉紅，
    他得先回去改掉那段「沒有呼叫端」的說明，順便決定要留哪一份實作。
    """
    assert _call_sites(symbol) == set(), f"{symbol} 已有呼叫端，其 docstring 的現況說明要更新"


@pytest.mark.parametrize(
    ("successor", "expected_module"),
    [
        ("order_admissible", "adjudication/adjudicator.py"),
        ("project_position", "ai_loop/world_view.py"),
        ("state_frames", "api/aar.py"),
        ("UnitMovementSystem", "sim_runtime.py"),
    ],
)
def test_successor_capability_is_still_wired(successor: str, expected_module: str) -> None:
    """抓的病：死函式的註解說「這件事現在由 X 做」——若 X 自己哪天被拔掉（這個 repo 出過
    好幾次「Protocol + NoOp 各一個、grep 不到第三個引用」），那句話就變成把讀者指向空地。

    釘住的是**指名的那個生產呼叫點**，不是「repo 裡某處有人呼叫」。
    """
    assert expected_module in _call_sites(successor)


def test_fratricide_accounting_still_flows_through_friendly_losses() -> None:
    """抓的病：`fratricide_victims` 的註解說誤傷歸帳改由事件的 `friendly_losses` 鍵承擔。

    那是一個**字串鍵**（不是函式），兩端一改名就靜默斷開——寫入端照寫、讀取端讀不到，
    FRATRICIDE 事件從此不再產生，而測試與型別檢查都不會有反應。
    這正是本 repo 的招牌病：值存在、被靜默忽略。
    """
    writer = (_APP / "adjudication" / "area_fire.py").read_text(encoding="utf-8")
    reader = (_APP / "engine" / "fire_wiring.py").read_text(encoding="utf-8")
    assert '"friendly_losses"' in writer
    assert '"friendly_losses"' in reader


# ── L4：過期敘述 ──────────────────────────────────────────────────────────


def test_system_config_module_doc_does_not_contradict_ai_loop_wired() -> None:
    """抓的病：`api/system.py` 的模組說明寫著「AI 決策迴路尚未接入活執行期 Kernel」，
    而同檔回給前端的 `ai_loop_wired` 早已是 True（O11 就接了）。

    前端 banner 讀旗標所以畫面沒錯，被誤導的是讀原始碼的人——包括會照著這句話
    「補接線」的下一個 agent。兩處只要再度分岔，本條轉紅。
    """
    import app.api.system as system_api

    src = (_APP / "api" / "system.py").read_text(encoding="utf-8")
    assert '"ai_loop_wired": True' in src
    doc = system_api.__doc__ or ""
    assert "已接入活執行期" in doc  # 說明要正面陳述現況，不能只是把舊句子刪掉
    assert "尚未接入" not in doc
