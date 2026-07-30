"""AI briefing 的欄位名要跟著資料來源走——**改名只改生產端會靜靜壞掉**。

2026-07-30 把 `unit_type`（名實不符：叫兵種、裝的是階層）拆成 `echelon` + `branch`，
但**渲染端沒跟著改**：`_fmt_ally` 讀 `u.get('unit_type', '?')`、
`_fmt_enemy` 的 extras 清單也還是舊鍵。後果是

- 己方/盟軍的編制在 prompt 裡變成「?」
- 敵情的 CLASSIFIED 級資訊（階層/兵科）**一個字都不會進 prompt**

而且**沒有任何錯誤**——`.get(key, '?')` 與清單過濾都是靜默的。
LLM 因此少掉一整類情報，卻沒有任何紅燈。這一檔就是那個紅燈。
"""

from __future__ import annotations

from app.ai_loop.context import _fmt_ally, _fmt_enemy


def test_ally_line_renders_the_echelon_not_a_question_mark() -> None:
    line = _fmt_ally(
        {
            "unit_id": "u1",
            "designation": "B1",
            "echelon": "COMPANY",
            "branch": "ARMOR",
            "faction": "BLUE",
            "lat": 24.0,
            "lng": 121.0,
        }
    )
    assert "COMPANY" in line, f"編制沒進 prompt：{line}"
    assert "ARMOR" in line, f"兵科沒進 prompt：{line}"
    assert "?" not in line, f"有欄位渲染成問號：{line}"


def test_ally_line_omits_unknown_branch_instead_of_printing_it() -> None:
    """`UNKNOWN` 是「沒指定」的中性值——把它印進 prompt 只會讓 LLM 以為那是一個兵科。"""
    line = _fmt_ally(
        {"unit_id": "u1", "designation": "B1", "echelon": "SQUAD", "branch": "UNKNOWN"}
    )
    assert "UNKNOWN" not in line, f"把 UNKNOWN 當成兵科印出來了：{line}"
    assert "SQUAD" in line


def test_enemy_line_carries_classified_level_intel() -> None:
    """CLASSIFIED 以上才有的階層/兵科要真的出現在敵情行裡。"""
    line = _fmt_enemy(
        {
            "unit_id": "e1",
            "faction": "RED",
            "designation": "R1",
            "echelon": "PLATOON",
            "branch": "INFANTRY",
            "last_seen_tick": 42,
        }
    )
    assert "PLATOON" in line, f"階層沒進敵情行：{line}"
    assert "INFANTRY" in line, f"兵科沒進敵情行：{line}"


def test_enemy_line_stays_empty_when_fidelity_reveals_nothing() -> None:
    """DETECTED 級只有位置與時間戳——不可以憑空生出編制描述。"""
    line = _fmt_enemy({"contact_id": "c1", "lat": 24.0, "lng": 121.0, "last_seen_tick": 7})
    for leaked in ("PLATOON", "INFANTRY", "RED"):
        assert leaked not in line, f"未達揭露等級卻出現 {leaked}：{line}"
