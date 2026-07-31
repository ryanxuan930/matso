"""劇本編輯器 UI ↔ 想定契約的覆蓋守衛（E6/E7/E8）。

這個 repo 反覆出事的型態是「值存在但沒有任何介面設得到」——`scenario.schema.json` 加了頂層設定、
後端讀得進來也用得到，但劇本編輯器一個欄位都沒有，想定作者只能去「匯入 JSON」文字框手貼。
下一個加設定的人不會知道要回來補 UI，而缺 UI 不會讓任何測試變紅。

所以這裡把「契約有的設定」與「編輯器畫得出的欄位」綁在一起：欄位以 `data-scenario-key="<鍵>"`
標記在 `scenario-editor.vue` 上，缺一個就紅。**唯讀檢查**，不需要瀏覽器也不需要 Nuxt 執行期。
（編輯器的匯出/匯入邏輯另有 `platform/tests/scenario-editor.test.ts`。）
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.fires.survivability import (
    _DEFAULT_MAX_KM,
    _DEFAULT_MIN_KM,
    _DEFAULT_MISSIONS,
)
from app.scenario.loader import ScenarioError, load_scenario_bundle

_REPO = Path(__file__).resolve().parents[3]
_SCHEMA_PATH = _REPO / "contracts" / "scenario.schema.json"
_EDITOR_PAGE = _REPO / "platform" / "app" / "pages" / "scenario-editor.vue"
_EDITOR_MODEL = _REPO / "platform" / "app" / "composables" / "useScenarioEditor.ts"

# 契約有、但**刻意**不放進劇本編輯器的頂層鍵，附上理由。
# 要新增條目請一併寫清楚「在哪裡編」——空白的豁免等於把這道守衛關掉。
_NOT_EDITED_HERE = {
    "files": "由 exportScenario 依 factions/msel 自動產生，不是人填的欄位",
    "no_strike_zones": "在 COP 的地圖編輯器畫（WP-A3）；劇本編輯器只原樣帶著不動它",
    # 同 no_strike_zones：帶座標的地圖物件在地圖上畫才對得準，表單裡填經緯度是自找麻煩。
    # COP 的地圖編輯器已有 SUPPLY_POINT 類別（`useMapFeatures.FEATURE_KINDS`），
    # 劇本編輯器則靠 passthrough 原樣帶著（未建模的頂層鍵會被 exportScenario 攤回去）。
    "supply_points": "在 COP 的地圖編輯器畫（WP-C7.2）；劇本編輯器只原樣帶著不動它",
}


def _schema() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return data


def _page() -> str:
    return _EDITOR_PAGE.read_text(encoding="utf-8")


def _model_source() -> str:
    return _EDITOR_MODEL.read_text(encoding="utf-8")


def _keys_with_ui(page: str) -> set[str]:
    return set(re.findall(r'data-scenario-key="([a-z_]+)"', page))


def test_every_scenario_setting_has_an_editor_field() -> None:
    """抓的病：契約有頂層設定、編輯器卻沒有任何欄位（E6/E7）。

    `request_quotas`／`day_night`／`allow_fratricide`／`indirect_fire_requires_approval`／
    `survivability_move` 曾經全部設不到（C2 的配額永遠「不限」、誤傷核取方塊永遠不出現、
    火協核准流程永遠觸發不了），bbox/tick_rate_ms 也一樣——新建的想定戰場永遠是寫死的預設值。
    """
    page = _page()
    missing = [
        key
        for key in _schema()["properties"]
        if key not in _NOT_EDITED_HERE and f'data-scenario-key="{key}"' not in page
    ]
    assert not missing, (
        f"scenario.schema.json 有這些頂層設定，但劇本編輯器沒有對應欄位：{missing}。"
        "請在 scenario-editor.vue 加欄位並標 data-scenario-key，"
        f"或在 {__name__} 的 _NOT_EDITED_HERE 寫明「不在這裡編」的理由。"
    )


def test_keys_with_ui_are_modelled_and_not_left_in_passthrough() -> None:
    """抓的病：有 UI 欄位卻沒列進 `MODELLED_SCENARIO_KEYS`（同一份狀態兩處寫入端）。

    匯入時未建模的鍵會被收進 `passthrough`，而 `exportScenario` 是「先攤開 passthrough、
    再覆蓋明確欄位」——使用者在 UI 把某項設定關掉時，passthrough 裡的舊值會被原封不動寫回去：
    畫面上關了、存出去的想定還開著，而且沒有任何錯誤訊息。
    """
    source = _model_source()
    block = re.search(r"MODELLED_SCENARIO_KEYS = new Set\(\[(.*?)\]\)", source, re.S)
    assert block, "找不到 MODELLED_SCENARIO_KEYS 宣告（useScenarioEditor.ts 結構被改動？）"
    modelled = set(re.findall(r"'([a-z_]+)'", block.group(1)))

    unmodelled = sorted(_keys_with_ui(_page()) - modelled)
    assert not unmodelled, (
        f"這些鍵在編輯器有欄位，卻沒列進 MODELLED_SCENARIO_KEYS：{unmodelled}——"
        "舊值會留在 passthrough，把使用者剛在 UI 關掉的設定復活。"
    )


def test_editor_only_offers_quota_kinds_the_contract_accepts() -> None:
    """抓的病：編輯器多給一種配額，產出的想定被 loader 整份拒載。

    `request_quotas` 是 `additionalProperties: false`。`CALL_FOR_FIRE` 雖然也是 RequestKind，
    但契約沒開放它的配額——多畫一格，作者存檔時只會看到一句 schema 錯誤。
    """
    kinds = re.search(r"REQUEST_QUOTA_KINDS = \[(.*?)\] as const", _model_source(), re.S)
    assert kinds, "找不到 REQUEST_QUOTA_KINDS 宣告"
    assert set(re.findall(r"'([A-Z_]+)'", kinds.group(1))) == set(
        _schema()["properties"]["request_quotas"]["properties"]
    )


def test_hex_resolution_is_locked_instead_of_being_a_rejected_dropdown() -> None:
    """抓的病：給使用者一個後端一定會拒絕的選項。

    loader 只接受 h3 res 8（core 十餘處寫死），宣告別的值當場拒載。編輯器若給下拉，
    作者會選一個看起來合法、存檔卻整份失敗的值——所以這一格必須是鎖定的唯讀顯示。
    """
    with pytest.raises(ScenarioError, match="hex_resolution"):
        load_scenario_bundle({"scenario": {**_minimal_scenario(), "hex_resolution": 9}})

    page = _page()
    field = re.search(r'data-scenario-key="hex_resolution".*?</label>', page, re.S)
    assert field, "找不到 hex_resolution 欄位"
    markup = field.group(0)
    assert "disabled" in markup, "hex_resolution 欄位必須是鎖定的（後端只收 8）"
    assert "<Select" not in markup, "hex_resolution 不可做成下拉：別的值後端一律拒載"


def test_turn_based_modes_are_labelled_as_unimplemented() -> None:
    """抓的病（E8）：編輯器提供「同步回合／輪流回合」，執行期卻完全沒有回合制分支。

    `WEGO`／`IGO_UGO` 在 core 只有 enum 定義，`sim_runtime`/engine 不分支——選了跑起來仍是即時制。
    在做出回合制之前，選項必須標示「尚未實作」；做出來之後，這個測試會反過來要求把標示拿掉。
    """
    implemented = [
        str(path.relative_to(_REPO))
        for path in (_REPO / "core" / "app").rglob("*.py")
        if path.name != "enums.py" and re.search(r"\bWEGO\b|\bIGO_UGO\b", path.read_text("utf-8"))
    ]
    page = _page()
    mode_lines = [line for line in page.splitlines() if re.search(r"value: '(WEGO|IGO_UGO)'", line)]
    assert len(mode_lines) == 2, "找不到 WEGO/IGO_UGO 兩個推演模式選項"

    if implemented:
        assert "UNIMPLEMENTED_MODE_SUFFIX" not in page, (
            f"回合制看起來已在 {implemented} 實作——請把編輯器上的「尚未實作」標示拿掉。"
        )
    else:
        for line in mode_lines:
            assert "UNIMPLEMENTED_MODE_SUFFIX" in line, (
                f"回合制模式仍未實作，此選項必須標示清楚：{line.strip()}"
            )


def test_survivability_defaults_match_the_backend() -> None:
    """抓的病：UI 勾「啟用陣地變換」時寫進想定的初值與後端預設漂掉。

    兩邊不一致時，作者以為自己沿用預設，實際上想定裡寫死了另一組數字——而且不會有任何錯誤訊息，
    只會在演習中發現砲兵換陣地的節奏跟預期不同。
    """
    block = re.search(r"SURVIVABILITY_DEFAULTS = \{(.*?)\} as const", _model_source(), re.S)
    assert block, "找不到 SURVIVABILITY_DEFAULTS 宣告"
    values = dict(re.findall(r"(\w+): ([\d.]+)", block.group(1)))
    assert float(values["missionsBeforeMove"]) == _DEFAULT_MISSIONS
    assert float(values["minKm"]) == _DEFAULT_MIN_KM
    assert float(values["maxKm"]) == _DEFAULT_MAX_KM


def _minimal_scenario() -> dict[str, Any]:
    return {
        "name": "編輯器輸出",
        "version": "1.0",
        "bbox": [119.0, 22.0, 122.5, 25.5],
        "mode": "REALTIME",
        "tick_rate_ms": 30000,
        "factions": [{"id": "BLUE"}, {"id": "RED"}],
        "victory_conditions": [
            {"faction": "BLUE", "condition": {"type": "faction_eliminated", "faction": "RED"}}
        ],
    }


def test_editor_shaped_settings_survive_the_backend_loader() -> None:
    """抓的病：編輯器畫得出欄位、匯出得出 JSON，後端卻載不進去或載進去沒帶著。

    這裡的 dict 逐鍵對齊 `exportScenario` 的輸出形狀（見 platform/tests/scenario-editor.test.ts），
    走的是編輯器存檔實際會呼叫的那條 `load_scenario_bundle`。任何一側的鍵名/型別漂掉都會在這裡紅。
    """
    loaded = load_scenario_bundle(
        {
            "scenario": {
                **_minimal_scenario(),
                "aggregate_adjudication_level": "BRIGADE",
                "request_quotas": {"AIR_RECON": 4, "FIRE_SUPPORT": 0},
                "day_night": {"sunrise_min": 340, "sunset_min": 1100, "start_min": 240},
                "allow_fratricide": True,
                "indirect_fire_requires_approval": True,
                "survivability_move": {
                    "enabled": True,
                    "missions_before_move": 2,
                    "min_km": 0.8,
                    "max_km": 3,
                },
            }
        }
    )
    assert loaded.tick_rate_ms == 30000
    assert loaded.bbox == [119.0, 22.0, 122.5, 25.5]
    assert loaded.aggregate_adjudication_level == "BRIGADE"
    # 配額 0 必須活著：0＝一張都不准提，與「未列＝不限」是不同的想定意圖。
    assert loaded.request_quotas == {"AIR_RECON": 4, "FIRE_SUPPORT": 0}
    assert loaded.day_night == {"sunrise_min": 340, "sunset_min": 1100, "start_min": 240}
    assert loaded.allow_fratricide is True
    assert loaded.indirect_fire_requires_approval is True
    assert loaded.survivability_move["missions_before_move"] == 2
