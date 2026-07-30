"""`UnitLevel` 的**宣告順序就是編制大小順序**——這件事沒有被寫下來過，但兩處在依賴它。

## 為什麼需要一條測試釘住

`aggregate.py:25` 與 `engine/comms.py:39` 都這樣算編制大小的秩：

    _SIZE_RANK = {level: rank for rank, level in enumerate(UnitLevel)}

再以 `rank <= _SIZE_RANK[BATTALION]` 判斷「營級以上＝指揮節點」。
也就是說**enum 的宣告順序直接決定模擬行為**。

在尾端追加一個新層級（最自然的做法）會讓它變成比 `INDIVIDUAL` 還小——
不會拋錯、不會有紅燈，只會讓聚合門檻與通信指揮節點的判定悄悄跑掉。
這條測試就是那個紅燈。
"""

from __future__ import annotations

from app.models.enums import UnitLevel

# 由大到小。**這份清單是規格，不是實作的副本**——改 enum 就要改這裡，
# 而改這裡的人會被迫想一下新層級到底多大。
EXPECTED_LARGEST_FIRST = [
    "THEATER",
    "ARMY_GROUP",
    "ARMY",
    "CORPS",
    "DIVISION",
    "BRIGADE",
    "REGIMENT",
    "BATTALION",
    "COMPANY",
    "PLATOON",
    "SECTION",
    "SQUAD",
    "FIRETEAM",
    "INDIVIDUAL",
]


def test_declaration_order_is_size_order() -> None:
    assert [level.value for level in UnitLevel] == EXPECTED_LARGEST_FIRST


def test_battalion_is_above_company_by_rank() -> None:
    """實際用到的那個比較：營比連大。

    只釘清單的話，若有人把清單與 enum 一起改錯（例如兩邊都倒過來）仍會綠。
    這條直接斷言語義。
    """
    rank = {level: i for i, level in enumerate(UnitLevel)}
    assert rank[UnitLevel.BATTALION] < rank[UnitLevel.COMPANY]
    assert rank[UnitLevel.THEATER] < rank[UnitLevel.INDIVIDUAL]
    # 新增的四級要落在正確的相對位置
    assert rank[UnitLevel.ARMY_GROUP] < rank[UnitLevel.ARMY] < rank[UnitLevel.CORPS]
    assert rank[UnitLevel.BRIGADE] < rank[UnitLevel.REGIMENT] < rank[UnitLevel.BATTALION]
    assert rank[UnitLevel.PLATOON] < rank[UnitLevel.SECTION] < rank[UnitLevel.SQUAD]


def test_the_command_node_threshold_still_means_battalion_and_above() -> None:
    """`engine/comms.py` 的指揮節點判定不可因為插入新層級而改變語義。"""
    from app.engine.comms import _COMMAND_RANK, _SIZE_RANK

    for level in (UnitLevel.THEATER, UnitLevel.ARMY, UnitLevel.BRIGADE, UnitLevel.BATTALION):
        assert _SIZE_RANK[level] <= _COMMAND_RANK, f"{level} 應為指揮節點"
    for level in (UnitLevel.COMPANY, UnitLevel.SECTION, UnitLevel.SQUAD, UnitLevel.INDIVIDUAL):
        assert _SIZE_RANK[level] > _COMMAND_RANK, f"{level} 不應為指揮節點"
