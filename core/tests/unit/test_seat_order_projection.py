"""席位 → 可下令型別的投影（UI-P2）。

COP 的下令下拉要能**先停用**這一席下不了的令型，否則作戰官會把火力任務的落點標好、
發數填完，按下送出才被 `ORDER_SEAT_DENIED` 彈回。

投影由後端算：`SEAT_ORDER_TYPES` 已經被漏改過兩次（作戰官少了
MISSION/POSTURE/FORMATION/ENGINEER、後勤官少了 RESUPPLY），前端再抄一份就是第三份。
"""

from __future__ import annotations

from app.lobby.service import _allowed_order_types
from app.models.enums import SeatRole
from app.orders.schemas import OrderType
from app.seats import SEAT_ORDER_TYPES


def test_the_projection_matches_the_authoritative_table_for_every_seat() -> None:
    """**逐席比對權威表**，不是抽查——漏一席的症狀是那一席的下拉憑空少幾個選項。"""
    for seat in SeatRole:
        projected = set(_allowed_order_types(seat.value))
        assert projected == {t.value for t in SEAT_ORDER_TYPES.get(seat, frozenset())}, seat


def test_an_unassigned_seat_is_not_filtered_at_all() -> None:
    """未指派席位回空 list ＝**不做席位過濾**（權限交還角色規則，既有局零變更）。"""
    assert _allowed_order_types(None) == []
    assert _allowed_order_types("") == []


def test_a_read_only_seat_also_projects_empty_and_that_is_not_the_same_thing() -> None:
    """情報官/觀察員也回空 list——**與「未指派」在資料上長得一樣**。

    兩者的差別由 `my_seat_role` 本身表達：null ＝未指派、非 null 而清單空 ＝唯讀。
    消費端只看清單就會把唯讀席位誤放成全開，所以這條把那個陷阱寫下來釘住。
    """
    assert _allowed_order_types(SeatRole.S2_INTEL.value) == []
    assert _allowed_order_types(SeatRole.OBSERVER.value) == []


def test_an_unknown_seat_string_falls_back_to_no_filtering_instead_of_crashing() -> None:
    """認不得的席位字串（舊資料、手改 DB）不該讓整個 lobby 列表 500。"""
    assert _allowed_order_types("NOT_A_SEAT") == []


def test_the_order_is_stable_and_follows_the_enum_not_the_set_hash() -> None:
    """回應體的欄位順序不該隨 set 的雜湊擺動——那會讓 API 回應每次重啟都不一樣。"""
    commander = _allowed_order_types(SeatRole.COMMANDER.value)
    assert commander == [t.value for t in OrderType if t.value in set(commander)]
