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


def test_aggregate_threshold_is_configurable_not_hardwired() -> None:
    """想定的 `aggregate_adjudication_level` 要真的能改變門檻。

    這一欄過去**載得進 LoadedScenario、也 dump 得出來（roundtrip 測試綠）
    卻從來沒有被持久化**，於是 `should_aggregate()` 一律吃自己的預設 BATTALION
    ——想定寫 COMPANY 或 BRIGADE 完全沒有作用，而且沒有任何測試會紅。
    """
    from app.adjudication.aggregate import should_aggregate

    # 預設門檻：營級以上聚合，連級以下不聚合
    assert should_aggregate(UnitLevel.BATTALION) is True
    assert should_aggregate(UnitLevel.COMPANY) is False
    # 想定把門檻降到連級 → 連也要聚合，排仍不聚合
    assert should_aggregate(UnitLevel.COMPANY, UnitLevel.COMPANY) is True
    assert should_aggregate(UnitLevel.PLATOON, UnitLevel.COMPANY) is False
    # 想定把門檻提高到旅級 → 營不再聚合
    assert should_aggregate(UnitLevel.BATTALION, UnitLevel.BRIGADE) is False
    assert should_aggregate(UnitLevel.BRIGADE, UnitLevel.BRIGADE) is True


def test_loader_leaves_the_default_threshold_as_null() -> None:
    """未宣告 / 明確宣告 BATTALION → 都存 None。

    留 None 而不是寫死 BATTALION：既有局的欄位是 NULL，寫死會讓「沒宣告」與
    「明確宣告 BATTALION」在資料上分不開，而前者才是絕大多數。
    """
    from app.scenario.loader import _agg_level

    assert _agg_level("BATTALION") is None
    assert _agg_level("") is None
    assert _agg_level("NOT_A_LEVEL") is None
    assert _agg_level("COMPANY") is UnitLevel.COMPANY
    assert _agg_level(" brigade ") is UnitLevel.BRIGADE


def test_the_adjudicator_actually_passes_its_configured_threshold() -> None:
    """裁決層呼叫 `should_aggregate` 時**必須把注入的門檻傳進去**。

    ⚠ 只測 `should_aggregate(level, threshold)` 本身是不夠的——那個函式一直都支援
    第二個參數，真正的 bug 是**呼叫端從來沒傳**。我第一版就是這樣寫的，
    把「回到寫死」這個突變放過去了。這條掃 AST，讓「有沒有傳」看得出來。
    """
    import ast
    import pathlib

    from app.adjudication import adjudicator as mod

    tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "should_aggregate"
    ]
    assert calls, "adjudicator 沒有呼叫 should_aggregate？"
    for call in calls:
        total = len(call.args) + len(call.keywords)
        assert total >= 2, (
            "should_aggregate 只傳了單位層級、沒傳門檻——"
            "想定的 aggregate_adjudication_level 又會變成沒有作用"
        )
