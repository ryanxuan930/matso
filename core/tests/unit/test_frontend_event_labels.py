"""後端發得出來的**每一種事件型別**，前端戰況小工具都必須有中文敘述。

## 為什麼這條測試比它翻譯的那些字串重要

後端有 ~50 種 `event_type`，`platform/app/composables/useCopFeed.ts` 原本只翻譯 7 種。
其餘全部落到退路，於是統裁與參謀在戰況小工具裡看到的是這種東西：

    MOVE_HALTED_FUEL
    GUARDRAIL_INTERVENTION
    MINE_STRIKE

**沒有番號、沒有原因。** 「MOVE_HALTED_FUEL」不會告訴指揮官是哪一支部隊沒油了。

補完那 40 幾條翻譯只要一個下午；真正的問題是**下一個新事件型別**——加事件的人不會
想到要去改一個前端 composable，於是幾個月後又長出一批裸代號。這條測試就是那道門：
新增 `event_type` 而沒補 `EVENT_LABELS` 的 commit 會直接紅。

## 為什麼由 pytest 掃前端檔案

`platform/` 目前沒有 vitest（package.json 只有 lint / typecheck / playwright），
為了守這一條而引進一整套前端測試框架，代價比缺陷本身大。而這條不變式的兩端**一端在
後端、一端在前端**——放在後端測試裡，反而是唯一能同時看到兩邊的位置。
用字串/AST 掃描而非執行 TS，也讓它不依賴 node 環境。

⚠ 若哪天 `useCopFeed.ts` 改名或 `EVENT_LABELS` 改成別的結構，要改的是**這條測試**，
不是刪掉它。它守的東西比它的實作方式重要。
"""

from __future__ import annotations

import ast
import pathlib
import re

_REPO = pathlib.Path(__file__).resolve().parents[3]
_CORE_APP = _REPO / "core" / "app"
_FEED_TS = _REPO / "platform" / "app" / "composables" / "useCopFeed.ts"
_BROADCASTER = _REPO / "core" / "app" / "state" / "broadcaster.py"


def _literal_strings(node: ast.AST) -> set[str]:
    """節點底下的字面字串。

    `ast.IfExp`（`"A" if cond else "B"`）只取兩個分支、**不取 test**——
    否則 `"ENGAGEMENT_RESOLVED" if resp.order_type == "ENGAGE" else "ORDER_VALIDATED"`
    會把比較用的 `"ENGAGE"` 誤收成事件型別。
    """
    if isinstance(node, ast.Constant):
        return {node.value} if isinstance(node.value, str) else set()
    if isinstance(node, ast.IfExp):
        return _literal_strings(node.body) | _literal_strings(node.orelse)
    return set()


def _publishers_with_event_type_param(trees: dict[pathlib.Path, ast.Module]) -> dict[str, int]:
    """函式名 → `event_type` 參數的位置索引。

    事件不只由 `LedgerEvent(event_type=...)` 產生：`stream/publish.py:publish_event`
    與 `api/c2.py:_push` 都是**位置參數**傳型別的轉發函式（C2_MESSAGE / SESSION_CONTROL
    都走這條路）。硬寫死函式名會漏；改成先讀簽章，日後多一個轉發函式也自動涵蓋。
    """
    out: dict[str, int] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            names = [a.arg for a in node.args.posonlyargs + node.args.args]
            if "event_type" in names:
                out[node.name] = names.index("event_type")
    return out


def backend_event_types() -> set[str]:
    """`core/app` 發得出來的所有事件型別字面量。"""
    trees = {p: ast.parse(p.read_text(encoding="utf-8")) for p in _CORE_APP.rglob("*.py")}
    forwarders = _publishers_with_event_type_param(trees)
    found: set[str] = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            # 1) 任何呼叫的 `event_type=` 關鍵字引數（LedgerEvent(...) 是大宗）
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "event_type":
                        found |= _literal_strings(kw.value)
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
                idx = forwarders.get(name)
                # 2) 轉發函式的位置引數（publish_event / _push）
                if idx is not None and len(node.args) > idx:
                    found |= _literal_strings(node.args[idx])
            # 3) 先算進區域變數再傳（obstacle_wiring / api.orders 都這樣寫）
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "event_type" for t in node.targets
            ):
                found |= _literal_strings(node.value)
    # 動態型別：MSEL 想定可注入自訂 event_type（`inject["event_type"]`），
    # 掃到的是鍵名字串本身，不是事件型別。
    found.discard("event_type")
    return found


def _ts_object_keys(source: str, const_name: str) -> set[str]:
    """從 TS 的物件字面量取鍵名（`export const X: ... = { KEY: '…', }`）。"""
    start = source.index(f"export const {const_name}")
    brace = source.index("{", start)
    end = source.index("\n}", brace)
    body = source[brace:end]
    return set(re.findall(r"^\s*([A-Z][A-Z0-9_]*):", body, flags=re.MULTILINE))


def _ts_array_items(source: str, const_name: str) -> set[str]:
    """從 TS 的陣列字面量取項目。**由 `= [` 起算**——型別標註 `readonly string[]`
    裡也有一對中括號，從常數名直接找 `]` 會停在型別上而抓到空集合。"""
    start = source.index(f"export const {const_name}")
    open_bracket = source.index("= [", start) + 2
    end = source.index("]", open_bracket)
    return set(re.findall(r"'([A-Z][A-Z0-9_]*)'", source[open_bracket:end]))


def test_every_backend_event_type_has_a_chinese_label() -> None:
    """抓的病：後端加了新事件型別，戰況小工具就多一行裸英文代號。

    這是本檔的主測試——`MOVE_HALTED_FUEL` / `GUARDRAIL_INTERVENTION` / `MINE_STRIKE`
    這批（37 種）當初就是這樣一路裸奔到指揮官眼前的。
    """
    src = _FEED_TS.read_text(encoding="utf-8")
    labelled = _ts_object_keys(src, "EVENT_LABELS")
    missing = sorted(backend_event_types() - labelled)
    assert not missing, (
        f"這些後端事件型別在 {_FEED_TS.name} 的 EVENT_LABELS 裡沒有中文敘述，"
        f"會在戰況小工具顯示成裸英文代號：{missing}"
    )


def test_hidden_event_list_matches_backend_feed_exclusion() -> None:
    """抓的病：後端把某個型別放行進 feed 了，前端卻還當它不會出現（或反之）。

    `EVENT_TYPES_NOT_IN_FEED` 是前端對「這種事件不該出現在戰況流」的宣告。它一旦與
    broadcaster 的 `_FEED_EXCLUDE` 漂開，就會有一整類事件在兩邊都沒人負責：
    後端開始推、前端當它不存在。
    """
    src = _BROADCASTER.read_text(encoding="utf-8")
    excl = re.search(r"_FEED_EXCLUDE\s*=\s*frozenset\(\{([^}]*)\}\)", src)
    assert excl is not None, "broadcaster.py 找不到 _FEED_EXCLUDE，這條測試需要更新"
    backend_excluded = set(re.findall(r'"([A-Z_]+)"', excl.group(1)))
    feed_src = _FEED_TS.read_text(encoding="utf-8")
    frontend_hidden = _ts_array_items(feed_src, "EVENT_TYPES_NOT_IN_FEED")
    assert backend_excluded == frontend_hidden, (
        f"後端 _FEED_EXCLUDE={sorted(backend_excluded)} 與前端 "
        f"EVENT_TYPES_NOT_IN_FEED={sorted(frontend_hidden)} 不一致"
    )


def test_white_cell_pause_is_visible_on_the_cop() -> None:
    """抓的病：白軍按暫停，其他席位的 COP 沒有任何提示。

    `POST /sessions/{id}/control` 會發 `SESSION_CONTROL`（payload 帶 action），但在
    `platform/app` 裡曾經**零命中**：白軍按下暫停後，其他參與者只看到模擬時鐘停住、
    事件流冒一行裸 `SESSION_CONTROL`——分不出是被暫停還是後端掛了。

    這條檢查「有沒有人在讀這個事件並據以顯示橫幅」，不檢查橫幅長什麼樣。
    """
    cop = (_REPO / "platform" / "app" / "pages" / "session" / "[id]" / "cop.vue").read_text("utf-8")
    assert "SESSION_CONTROL" in cop, "cop.vue 沒有消費 SESSION_CONTROL，白軍暫停不會有任何提示"
    assert "pause-banner" in cop, "cop.vue 有讀 SESSION_CONTROL 但沒有暫停橫幅"


def test_label_table_has_no_entries_for_events_nobody_emits() -> None:
    """抓的病：事件型別被後端刪掉/改名，前端的翻譯留在原地變成死條目。

    死條目本身不會壞畫面，但它會讓下一個人以為那種事件還在發——查半天才發現
    整條路徑早就不存在了。
    """
    src = _FEED_TS.read_text(encoding="utf-8")
    labelled = _ts_object_keys(src, "EVENT_LABELS")
    stale = sorted(labelled - backend_event_types())
    assert not stale, f"EVENT_LABELS 有後端已不再發出的事件型別：{stale}"
