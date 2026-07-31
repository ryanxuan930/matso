"""WP-C7.2 補給點的建立路徑（在此之前**只有測試建得出補給點**）。

## 這一檔真正在守的東西

`engine/supply_points.py` 的查詢端早就完整了：`load_points` / `nearest_usable` /
`draw_from` / `destroy_at` 都有測試、都會過。但整條鏈在真實的一局裡一次都跑不到，
因為 **`MapFeature(kind="SUPPLY_POINT")` 沒有任何生產建立路徑**——不在想定 schema、
不在 MSEL、不在前端類別清單。查詢端再完整，也只是在查一張永遠是空的表。

所以本檔每一條測試的形狀都是：**打真的 HTTP 端點建立，再用引擎自己的
`load_points()` 讀回來**。中間不摸 DB、不自己造 `MapFeature`——那樣就變成在驗
「我塞進去的東西讀得出來」，而那正是這個 repo 反覆出現的假綠。

## 三道 422 為什麼是必要的，而不是多餘的嚴格

`read_point()` 對壞資料一律**靜默回 None**（略過該筆，不讓一筆髒資料毀掉整局的補給）。
那個選擇是對的，但它的代價是：白軍在 COP 上圈了一個補給點、清單裡有、地圖上畫得出來，
而撥交端根本不認得它——**沒有任何跡象**。把話講在建立當下，是唯一還來得及的時機。
"""

from __future__ import annotations

import pathlib
import re
from collections.abc import Iterator
from typing import Any

import pytest
import yaml
from _auth_fakes import TEST_SETTINGS
from _order_fakes import OrderWorld, order_token, seed_world
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.adjudication.supply import STARVATION_STEPS, SupplyClass
from app.api.deps import get_db, get_settings
from app.engine.supply_points import SUPPLY_POINT_KIND, load_points, nearest_usable
from app.main import app
from app.models import UserRole

_REPO = pathlib.Path(__file__).resolve().parents[3]
_CONTRACT = _REPO / "contracts" / "core_api.yaml"
_FEATURES_TS = _REPO / "platform" / "app" / "composables" / "useMapFeatures.ts"
_LABELS_TS = _REPO / "platform" / "app" / "composables" / "useLabels.ts"

# 補給點就放在 seed_world 的藍軍單位旁（DRAW_RADIUS_M = 3 km 之內）。
_BLUE_LAT, _BLUE_LNG = 23.75, 121.25
_POINT = {
    "kind": SUPPLY_POINT_KIND,
    "geometry_type": "POINT",
    "geometry": [_BLUE_LNG, _BLUE_LAT],
    "owner_faction": "BLUE",
    "label": "藍軍前進補給點",
    "attributes": {"stock": {"I": 500, "IX": 80}},
}


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> TestClient:
    def _db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS
    return TestClient(app)


@pytest.fixture
def world(session_factory: sessionmaker[Session]) -> OrderWorld:
    return seed_world(session_factory)


def _white(w: OrderWorld) -> dict[str, str]:
    return {"Authorization": f"Bearer {order_token(w.white_user_id, UserRole.WHITE_CELL_STAFF)}"}


def _base(w: OrderWorld) -> str:
    return f"/api/v1/sessions/{w.session_id}/map-features"


def _post(client: TestClient, w: OrderWorld, **overrides: Any) -> Any:
    return client.post(_base(w), json={**_POINT, **overrides}, headers=_white(w))


def test_a_supply_point_drawn_on_the_cop_is_readable_by_the_drawing_engine(
    client: TestClient, world: OrderWorld, session_factory: sessionmaker[Session]
) -> None:
    """**本檔的主測試**：走 HTTP 建立 → 引擎的 `load_points()` 撥交端讀得到它。

    只斷言 201 是不夠的——`read_point()` 會靜默略過它讀不懂的列，所以「建立成功」
    與「撥交端看得見」是兩件事，而畫面上分不出來。這裡把兩端接起來。
    """
    assert _post(client, world).status_code == 201

    with session_factory() as db:
        points = load_points(db, world.session_id)
    assert len(points) == 1, "白軍圈的補給點，撥交端一個都沒讀到"
    point = points[0]
    assert point.faction == "BLUE"
    assert point.stock == {SupplyClass.I: 500.0, SupplyClass.IX: 80.0}
    assert point.usable

    # 再往前一步：藍軍單位真的挑得到它（`nearest_usable` 是 auto_resupply 的第一道關）。
    assert nearest_usable(points, "BLUE", _BLUE_LAT, _BLUE_LNG) is point
    assert nearest_usable(points, "RED", _BLUE_LAT, _BLUE_LNG) is None, "敵軍不該拉得到藍軍的補給"


def test_editing_stock_reaches_the_drawing_engine_too(
    client: TestClient, world: OrderWorld, session_factory: sessionmaker[Session]
) -> None:
    """PATCH 改庫存也要走到引擎——建立通了、編輯沒通的話，白軍調不動任何一格。

    順帶釘住 `stock` 是**整包換掉**：把 IX 改成不備，撥交端就不該再撥得出 IX
    （attributes 是 merge 語義，只送新的 `I` 會讓舊的 `IX` 活下來）。
    """
    fid = _post(client, world).json()["id"]
    r = client.patch(
        f"{_base(world)}/{fid}", json={"attributes": {"stock": {"I": 120}}}, headers=_white(world)
    )
    assert r.status_code == 200, r.text

    with session_factory() as db:
        point = load_points(db, world.session_id)[0]
    assert point.stock == {SupplyClass.I: 120.0}


def test_a_supply_point_stored_as_an_area_is_refused(client: TestClient, world: OrderWorld) -> None:
    """存成面的補給點 `read_point()` 解不開 → 整筆略過。**畫面上與有效的補給點一模一樣。**"""
    r = _post(
        client,
        world,
        geometry_type="POLYGON",
        geometry=[[121.2, 23.7], [121.3, 23.7], [121.3, 23.8]],
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "MAP_FEATURE_SUPPLY_POINT_GEOMETRY"


def test_a_supply_point_on_the_common_layer_is_refused(
    client: TestClient, world: OrderWorld
) -> None:
    """白軍不指定陣營 → 落在 WHITE_CELL 共同層 → `nearest_usable` 只找同陣營的，一個都撥不出去。

    這是白軍最容易踩的一步：其他每一種標註都可以放共同層，只有補給點不行。
    """
    r = _post(client, world, owner_faction=None)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "MAP_FEATURE_SUPPLY_POINT_FACTION"


def test_a_typo_in_the_supply_class_is_refused(client: TestClient, world: OrderWorld) -> None:
    """認不得的類別 `read_point()` 是 `continue`——那一格庫存人間蒸發，且沒有任何訊息。"""
    r = _post(client, world, attributes={"stock": {"II": 500}})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "MAP_FEATURE_SUPPLY_POINT_STOCK"

    # 沒宣告庫存的補給點同理：它在圖上與有貨的長得一樣，卻撥不出任何東西。
    assert _post(client, world, attributes={}).status_code == 422


def test_other_feature_kinds_keep_their_freedom(client: TestClient, world: OrderWorld) -> None:
    """三道檢查**只對補給點生效**。障礙/標註照樣可以是面、可以落共同層、可以沒有 attributes。

    不釘住這條的話，這張卡就會變成「把整個地圖編輯器一起收緊」的回歸來源。
    """
    r = client.post(
        _base(world),
        json={
            "kind": "OBSTACLE",
            "geometry_type": "POLYGON",
            "geometry": [[121.2, 23.7], [121.3, 23.7], [121.3, 23.8]],
        },
        headers=_white(world),
    )
    assert r.status_code == 201, r.text
    assert r.json()["owner_faction"] == "WHITE_CELL"


def test_a_rejected_edit_leaves_the_feature_untouched(
    client: TestClient, world: OrderWorld, session_factory: sessionmaker[Session]
) -> None:
    """檢查跑在**套用之後**（要看 merge 結果）→ 失敗必須整批回滾。

    不回滾的話，一次被拒的 PATCH 仍然會把幾何/標籤改掉：使用者看到 422，
    以為什麼都沒發生，而地圖上的補給點已經被搬走了。
    """
    fid = _post(client, world).json()["id"]
    r = client.patch(
        f"{_base(world)}/{fid}",
        json={"label": "改壞的", "attributes": {"stock": {"II": 1}}},
        headers=_white(world),
    )
    assert r.status_code == 422

    got = client.get(_base(world), headers=_white(world)).json()[0]
    assert got["label"] == "藍軍前進補給點"
    with session_factory() as db:
        assert load_points(db, world.session_id)[0].stock == {
            SupplyClass.I: 500.0,
            SupplyClass.IX: 80.0,
        }


# ---- 跨檔漂移閘門（一端在後端、一端在契約/前端，只有這裡同時看得到兩邊）----


def test_the_supply_point_kind_string_is_the_same_in_all_three_places() -> None:
    """`SUPPLY_POINT` 這個字串寫在三個地方，打錯一個字母就是「畫得出來、撥交端讀不到」。

    ①引擎常數 ②契約的 kind 說明（前端照它決定要不要支援）③前端的 `FEATURE_KINDS`
    ——第三處是使用者唯一的入口，漏了它整條鏈就沒有起點（這正是這張卡的起因）。
    """
    spec = yaml.safe_load(_CONTRACT.read_text("utf-8"))
    kind_doc = spec["components"]["schemas"]["MapFeatureView"]["properties"]["kind"]["description"]
    assert SUPPLY_POINT_KIND in kind_doc, "契約的 kind 說明沒有列出補給點"

    ts = _FEATURES_TS.read_text("utf-8")
    assert re.search(rf"SUPPLY_POINT_KIND\s*=\s*'{SUPPLY_POINT_KIND}'", ts), (
        "前端常數與引擎的 SUPPLY_POINT_KIND 不一致"
    )
    # ⚠ **一定要限定在 `FEATURE_KINDS` 陣列裡面找**。第一版是整檔搜字串，於是
    # 上面那行常數宣告自己就把斷言餵飽了——把補給點從下拉清單整條刪掉，測試照樣綠。
    # （突變測試抓出來的：這正是「斷言通過但理由是錯的」。）
    kinds = re.search(r"FEATURE_KINDS\s*=\s*\[(.*?)\n\]", ts, re.S)
    assert kinds, "前端 useMapFeatures.ts 找不到 FEATURE_KINDS（改名了就把這條測試一起改，不要刪）"
    assert re.search(rf"value:\s*(SUPPLY_POINT_KIND|['\"]{SUPPLY_POINT_KIND}['\"])", kinds[1]), (
        "前端 FEATURE_KINDS 沒有補給點——白軍在 COP 上圈不出來，整條後勤鏈沒有起點"
    )


def test_the_frontend_starvation_ladder_mirrors_the_backend() -> None:
    """斷補效能階梯在前端有一份鏡像（單位卡要把「斷補 3 日」翻成「效能 ×0.5」）。

    後端只把**天數**推進 STATE_DIFF，倍率是前端算的——兩份會漂，漂了的症狀是
    卡片上的數字與裁決層實際套用的倍率不一樣，而兩邊各自都「正確」。
    改了 `STARVATION_STEPS` 沒改前端（或反之）這條就紅。
    """
    ts = _LABELS_TS.read_text("utf-8")
    block = re.search(r"STARVATION_STEPS[^=]*=\s*\[(.*?)\n\]", ts, re.S)
    assert block, "前端 useLabels.ts 找不到 STARVATION_STEPS（改名了就把這條測試一起改，不要刪）"
    pairs = [
        (float(a), float(b)) for a, b in re.findall(r"\[\s*([\d.]+)\s*,\s*([\d.]+)\s*\]", block[1])
    ]
    assert pairs == [(float(d), float(m)) for d, m in STARVATION_STEPS]
