"""契約 `OrderType` 的每一個值，指令小工具都必須有中文標籤。

## 為什麼需要這一條

`ORDER_TYPE_LABELS` 原本只有四種（MOVE/ENGAGE/FIRE_MISSION/POSTURE），但下令面板
早就給了七種令型（多了 MISSION/FORMATION/ENGINEER），契約的 `OrderType` 更有九種
（另有 RECON/RESUPPLY）。缺的那幾種不會報錯——`orderTypeLabel` 查不到就回傳原字串，
於是指令列上出現「第1工兵連 ENGINEER」。

這正是這個 codebase 反覆出現的病型：**存得進去、讀得回來、測試全綠、顯示是英文代號**。
翻譯本身兩分鐘就補完了，缺的是一道會在下次加令型時擋下來的門。

同一份 enum 也守 `MissionType`（任務型）與 `MissionPhase`（任務階段），理由相同。
"""

from __future__ import annotations

import pathlib
import re

import yaml

_REPO = pathlib.Path(__file__).resolve().parents[3]
_CONTRACT = _REPO / "contracts" / "core_api.yaml"
_ORDERS_TS = _REPO / "platform" / "app" / "composables" / "useOrders.ts"


def _contract_enum(name: str) -> set[str]:
    schemas = yaml.safe_load(_CONTRACT.read_text(encoding="utf-8"))["components"]["schemas"]
    return set(schemas[name]["enum"])


def _ts_object_keys(const_name: str) -> set[str]:
    """從 TS 的物件字面量取鍵名（`export const X: Record<…> = { KEY: '…', }`）。"""
    src = _ORDERS_TS.read_text(encoding="utf-8")
    start = src.index(f"export const {const_name}")
    brace = src.index("{", start)
    body = src[brace : src.index("\n}", brace)]
    return set(re.findall(r"^\s*([A-Z][A-Z0-9_]*):", body, flags=re.MULTILINE))


def test_every_order_type_has_a_chinese_label() -> None:
    """抓的病：契約新增令型（或既有令型漏翻），指令小工具就印英文代號給參謀看。"""
    missing = sorted(_contract_enum("OrderType") - _ts_object_keys("ORDER_TYPE_LABELS"))
    assert not missing, f"useOrders.ts 的 ORDER_TYPE_LABELS 缺少這些令型的中文標籤：{missing}"


def test_every_order_status_has_a_chinese_label() -> None:
    """抓的病：狀態機新增狀態而前端沒跟上，指令列的狀態欄變成英文。"""
    missing = sorted(_contract_enum("OrderStatus") - _ts_object_keys("ORDER_STATUS_LABELS"))
    assert not missing, f"useOrders.ts 的 ORDER_STATUS_LABELS 缺少這些狀態的中文標籤：{missing}"


def test_every_mission_type_and_phase_has_a_chinese_label() -> None:
    """抓的病：任務級下令（WP-A2）新增任務型/階段，指令列顯示 SEIZE / CONSOLIDATING。"""
    missing_type = sorted(_contract_enum("MissionType") - _ts_object_keys("MISSION_TYPE_LABELS"))
    missing_phase = sorted(_contract_enum("MissionPhase") - _ts_object_keys("MISSION_PHASE_LABELS"))
    assert not missing_type, f"MISSION_TYPE_LABELS 缺少：{missing_type}"
    assert not missing_phase, f"MISSION_PHASE_LABELS 缺少：{missing_phase}"


def test_orders_panel_reads_parent_order_and_mission_phase() -> None:
    """抓的病：後端回了欄位，前端零讀取。

    `OrderResponse.parent_order_id`（任務分解出的子令）與 `mission_phase`（任務階段）
    在指令小工具裡曾經一次都沒被讀過。後果很具體：任務子令與人親手下的令混在同一個
    平面清單裡，看起來像「這支部隊收到一堆沒人下過的命令」；而任務令從頭到尾只顯示
    「執行中」，看不出跑到哪一階段。

    ⚠ `mission_phase` 目前**後端還沒填**（`core/app/orders/schemas.py` 的
    `OrderResponse` 沒有這個欄位，契約有）。這條測試守的是前端這一半不要再被拔掉；
    後端補上 `_to_response` 之後畫面才會真的亮起來。
    """
    panel = (_REPO / "platform" / "app" / "components" / "cop" / "OrdersPanel.vue").read_text(
        encoding="utf-8"
    )
    assert "parent_order_id" in panel, "OrdersPanel 沒有讀 parent_order_id，子令會混在平面清單裡"
    assert "mission_phase" in panel, "OrdersPanel 沒有讀 mission_phase，任務階段永遠看不到"


def test_label_tables_have_no_entries_the_contract_dropped() -> None:
    """抓的病：契約砍掉某個值，前端翻譯留在原地，讓人以為那個令型還能下。"""
    stale = sorted(_ts_object_keys("ORDER_TYPE_LABELS") - _contract_enum("OrderType"))
    assert not stale, f"ORDER_TYPE_LABELS 有契約已無的令型：{stale}"
