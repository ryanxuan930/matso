"""ROE 的盟軍保護只有在**呼叫端注入該局關係矩陣**時才真的存在（WP-C9 先決）。

## 這條在補之前是壞的

`_precheck_engage` 的 ROE 分支寫 `if not relations.is_hostile(shooter, target)`，
註解寫「打盟軍/中立一律拒」。但 `run_precheck` 在 `relations=None` 時退回
`FactionRelations()`＝**全 HOSTILE 預設**，而 `is_hostile("BLUE","GREEN")` 在那份矩陣裡是
**True**。所以那條分支只有在打**自己陣營**時才成立。

而 `api/deps.py` 的 `get_order_service`——**人類指揮官下的每一道令**走的那一條——
從來沒有傳過 relations。結果：

- 人類：可以對盟軍下 ENGAGE，預檢照過。
- AI（`orders_bridge.py` 有傳 relations）：擋得住。

**恰好倒過來**：受約束的是機器，不受約束的是人。

這是 fail-open 的典型形狀：預設值看起來保守（「全部當敵人」），但套進一條
「非敵對就拒絕」的規則裡，它是最寬鬆的那個值。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_CORE_APP = pathlib.Path(__file__).resolve().parents[2] / "app"


# ---- 行為：盟軍真的打不了 ----


def _allied_world(session_factory):  # type: ignore[no-untyped-def]
    """把 RED 改成 BLUE 的盟軍，並把關係矩陣寫進 session。"""
    from _order_fakes import seed_world

    from app.models.tables import TacticalUnit, WargameSession

    world = seed_world(session_factory)
    with session_factory() as db:
        db.get(TacticalUnit, world.red_unit_id).faction = "GREEN"
        # ⚠ 格式是**三元組陣列**，不是 dict。`relations_from_triples` 對認不得的形狀
        # 一律靜靜跳過並退回全 HOSTILE——寫錯形狀不會噴錯，只會讓盟軍保護消失。
        db.get(WargameSession, world.session_id).faction_relations = [["BLUE", "GREEN", "ALLIED"]]
        db.commit()
    return world


def test_the_default_matrix_calls_a_different_faction_hostile(session_factory) -> None:  # type: ignore[no-untyped-def]
    """先釘住這個前提——後面兩條測試的意義全靠它。

    `FactionRelations()` 對**不同**陣營一律回 HOSTILE，只有同陣營才是 ALLIED。
    所以「非敵對就拒」這條規則配上這份預設值＝只擋自己人。
    """
    from app.factions.relations import FactionRelations

    default = FactionRelations()
    assert default.is_hostile("BLUE", "GREEN") is True
    assert default.is_hostile("BLUE", "BLUE") is False


def test_engaging_an_ally_is_rejected_when_relations_are_injected(session_factory) -> None:  # type: ignore[no-untyped-def]
    from _order_fakes import FakeGateway

    from app.errors import PrecheckFailedError
    from app.factions.session_store import load_session_relations
    from app.orders.schemas import OrderRequest, OrderType
    from app.orders.service import OrderService

    world = _allied_world(session_factory)
    db = session_factory()
    svc = OrderService(db, FakeGateway(), relations=load_session_relations(db, world.session_id))
    with pytest.raises(PrecheckFailedError, match="非敵對"):
        svc.submit(
            world.session_id,
            OrderRequest(
                unit_id=world.blue_unit_id,
                order_type=OrderType.ENGAGE,
                payload={"target_unit_id": world.red_unit_id},
            ),
            world.blue_issuer_id,
        )
    db.close()


def test_without_relations_the_same_order_sails_through(session_factory) -> None:  # type: ignore[no-untyped-def]
    """**這一條記錄的是壞掉的樣子**，不是期望行為。

    同一道「打盟軍」的令，`relations` 沒傳就通過預檢。留著它是為了讓
    「有沒有注入」這件事的差別是**可執行的**，而不是只寫在註解裡。
    上面那條結構測試負責保證正式路徑不會走到這裡。
    """
    from _order_fakes import FakeGateway

    from app.orders.schemas import OrderRequest, OrderType
    from app.orders.service import OrderService

    world = _allied_world(session_factory)
    db = session_factory()
    resp = OrderService(db, FakeGateway()).submit(  # relations 刻意不傳
        world.session_id,
        OrderRequest(
            unit_id=world.blue_unit_id,
            order_type=OrderType.ENGAGE,
            payload={"target_unit_id": world.red_unit_id},
        ),
        world.blue_issuer_id,
    )
    assert resp.status.value == "VALIDATED"  # ← 打盟軍卻放行
    db.close()


# ---- 結構：正式路徑一個都不許漏 ----
#
# 行為測試擋不住「下一個忘記注入的呼叫端」——那正是這個 bug 的形狀（服務寫對了、
# 規則寫對了、就是有一個組裝點沒傳參數）。同 `test_live_kernel_wiring.py` 的理由，
# 用 AST 驗組裝點。

# 允許不傳 relations 的檔案：純測試輔助或無 session 脈絡者。目前**沒有**。
_EXEMPT: frozenset[str] = frozenset()


def _order_service_calls() -> list[tuple[str, int, ast.Call]]:
    out: list[tuple[str, int, ast.Call]] = []
    for path in sorted(_CORE_APP.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "OrderService"
            ):
                out.append((str(path.relative_to(_CORE_APP.parent)), node.lineno, node))
    return out


def test_every_production_order_service_injects_relations() -> None:
    """`core/app/` 裡每一個 `OrderService(...)` 都要帶 `relations=`。

    漏傳**不會噴任何錯**（那是有預設值的具名參數），只會讓那條路徑的 ROE 盟軍保護
    靜靜消失。`api/deps.py` 就是這樣漏了——而它是人類下令的唯一入口。
    """
    calls = _order_service_calls()
    assert calls, "找不到任何 OrderService 建構點——這條測試的前提壞了"
    offenders = [
        f"{f}:{line}"
        for f, line, node in calls
        if f not in _EXEMPT and "relations" not in {kw.arg for kw in node.keywords if kw.arg}
    ]
    assert not offenders, (
        f"這些 OrderService 建構點沒有注入 relations，該路徑的 ROE 盟軍保護等於不存在：{offenders}"
    )
