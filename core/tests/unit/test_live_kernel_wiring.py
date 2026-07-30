"""活執行期的 Kernel **每一個子系統槽都必須綁到真實實作**，不是 NoOp。

## 為什麼需要這一條

這個缺陷已經發生過兩次，兩次都是「槽留好了、實作寫好了、就是沒有人把它接上」：

1. **WP-B2 MSEL**：`sim_runtime` 傳的是 `NoOpTriggerChecker` → MSEL 引擎從來沒有被呼叫過。
2. **WP-A2 任務令**：`mission_planner` 根本沒傳 → 吃到預設的 `NoOpMissionPlanner`，
   MISSION 令收得下、狀態變 VALIDATED、指令列看得到，**然後什麼都不會發生**。
   使用者回報「任務的功能好像異常」時，整套測試是綠的。

兩次都沒有任何測試會紅——因為**沒有任何測試碰得到組裝點**（composition root）。
把 planner 換回 `None` 跑整包 `core/tests`：1572 passed。這條測試就是要讓那個突變變紅。

## 為什麼用 AST 而不是真的起一個 Kernel

`SimManager._run_session` 是一個要 Redis、DB、地形服務與 async 迴圈的長方法；
為了驗一行參數而把它拆開，代價比這個缺陷本身還大。組裝點的不變式是**語法層**的
（「這個關鍵字參數有沒有綁到 NoOp」），用 AST 驗剛好對得上，而且不會因為執行環境而漂移。

⚠ 若哪天 Kernel 的組裝改成走 builder/工廠，這條會誤紅——那時要改的是**這條測試**，
不是把它刪掉。它守的東西比它的實作方式重要。
"""

from __future__ import annotations

import ast
import pathlib

_SIM_RUNTIME = pathlib.Path(__file__).resolve().parents[2] / "app" / "sim_runtime.py"
_SUBSYSTEMS = pathlib.Path(__file__).resolve().parents[2] / "app" / "engine" / "subsystems.py"

# 活執行期一定要有真實實作的槽。**廣播器與事件槽也在內**——那兩個靜靜變成 NoOp
# 的話，前端會看到一個永遠不動的戰場而後端毫無錯誤。
_REQUIRED_SLOTS = {
    "order_source",
    "adjudicator",
    "movement",
    "sensors",
    "comms",
    "logistics",
    "trigger_checker",
    "mission_planner",
    "broadcaster",
    "event_sink",
}


def _live_kernel_call() -> ast.Call:
    tree = ast.parse(_SIM_RUNTIME.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Kernel"
    ]
    assert len(calls) == 1, f"sim_runtime 有 {len(calls)} 個 Kernel 建構點，預期剛好 1 個"
    return calls[0]


def _noop_class_names() -> set[str]:
    tree = ast.parse(_SUBSYSTEMS.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name.startswith("NoOp")
    }


def _root_names(node: ast.AST) -> set[str]:
    """一個運算式裡出現的所有名字（含巢狀呼叫，如 `ChainedOrderSource(A(), B())`）。"""
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def test_every_live_subsystem_slot_is_filled() -> None:
    """每個必填槽都要在活執行期的 Kernel 呼叫裡出現。

    **漏傳一個槽不會噴 TypeError**——`mission_planner` 有預設值，
    `checkpointer` 也有。WP-A2 就是這樣靜靜吃了半年的 NoOp。
    """
    provided = {kw.arg for kw in _live_kernel_call().keywords if kw.arg}
    missing = _REQUIRED_SLOTS - provided
    assert not missing, f"活執行期的 Kernel 沒有傳這些槽（會吃到預設值/NoOp）：{sorted(missing)}"


def test_no_live_subsystem_slot_is_wired_to_a_noop() -> None:
    """真正的那一條：綁到 NoOp 等於這個子系統在正式推演裡完全不存在。"""
    noops = _noop_class_names()
    assert noops, "subsystems.py 找不到任何 NoOp 類別——這條測試的前提壞了"
    offenders = []
    for kw in _live_kernel_call().keywords:
        if kw.arg in _REQUIRED_SLOTS and _root_names(kw.value) & noops:
            offenders.append(kw.arg)
    assert not offenders, f"活執行期的 Kernel 把這些槽綁到 NoOp：{sorted(offenders)}"


def test_no_live_subsystem_slot_is_none() -> None:
    """`mission_planner=None` 與傳 NoOp 完全等價（Kernel 的 `or NoOpMissionPlanner()`），
    但 grep 「NoOp」找不到它——所以要分開驗。"""
    offenders = [
        kw.arg
        for kw in _live_kernel_call().keywords
        if kw.arg in _REQUIRED_SLOTS
        and isinstance(kw.value, ast.Constant)
        and kw.value.value is None
    ]
    assert not offenders, f"活執行期的 Kernel 把這些槽傳成 None：{sorted(offenders)}"
