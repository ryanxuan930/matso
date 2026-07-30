"""煙幕（WP-C4c）：雙面遮蔽、消散、接線、發煙任務。

[JCATS-A p.19]：煙幕是化學兵的標準配屬，作用是阻視線。
"""

from __future__ import annotations

from app.adjudication.obscurants import (
    DEFAULT_SMOKE_RADIUS_M,
    SmokeCloud,
    active,
    blocks_los,
    duration_ticks,
)

# 台灣本島陸地上的一段短視線（約 1.1 km）。
_A = (24.000, 121.000)
_B = (24.010, 121.000)


def _cloud(lat: float, lng: float, *, radius: float = 200.0, expires: int = 100) -> SmokeCloud:
    return SmokeCloud(lat=lat, lng=lng, radius_m=radius, expires_at_tick=expires)


# ---- 中性：沒有煙就什麼都不做 ----


def test_no_smoke_never_blocks_anything() -> None:
    """既有局一片煙都沒有 → 這條路徑零成本、零行為變更。"""
    assert blocks_los(_A, _B, [], tick=0) is False
    assert active([], 0) == []


def test_expired_smoke_does_not_block() -> None:
    """消散是**到期即消失**，不做濃度衰減——那條路要回到「係數」，而係數會讓
    「隔著煙幕狙擊」變成機率低但可行的事。"""
    cloud = _cloud(24.005, 121.000, expires=10)
    assert blocks_los(_A, _B, [cloud], tick=9) is True
    assert blocks_los(_A, _B, [cloud], tick=10) is False
    assert active([cloud], 10) == []


def test_a_zero_radius_cloud_is_inert() -> None:
    assert blocks_los(_A, _B, [_cloud(24.005, 121.0, radius=0.0)], tick=0) is False


# ---- 幾何 ----


def test_smoke_on_the_line_of_sight_blocks_it() -> None:
    assert blocks_los(_A, _B, [_cloud(24.005, 121.000)], tick=0) is True


def test_smoke_beside_the_line_of_sight_does_not() -> None:
    """離視線夠遠的煙不擋——半徑 200 m，煙心擺在側向約 1 km 處。"""
    assert blocks_los(_A, _B, [_cloud(24.005, 121.010)], tick=0) is False


def test_smoke_behind_the_observer_does_not_block() -> None:
    """**線段不是無限直線**：射手背後的煙擋不到他往前看。

    用點到**直線**的距離就會誤判這一種——`dist_point_to_segment_m` 會把 t 夾在 [0,1]。
    """
    assert blocks_los(_A, _B, [_cloud(23.980, 121.000)], tick=0) is False


def test_any_one_cloud_is_enough() -> None:
    clouds = [_cloud(24.005, 121.010), _cloud(24.005, 121.000)]
    assert blocks_los(_A, _B, clouds, tick=0) is True


# ---- 雙面 ----


def test_smoke_blocks_both_directions_identically() -> None:
    """**規格明寫煙幕是雙面的**，而這決定了整個介面的形狀：`blocks_los` 不知道誰是誰。

    放煙的一方同樣看不穿自己的煙——那正是煙幕在戰術上要付的代價（掩護退卻的煙
    也擋住你自己的觀測）。任何帶 `faction` 參數的版本都會把這件事弄丟。
    """
    clouds = [_cloud(24.005, 121.000)]
    assert blocks_los(_A, _B, clouds, tick=0) == blocks_los(_B, _A, clouds, tick=0) is True


def test_blocks_los_takes_no_faction_argument() -> None:
    """把上一條釘成結構性的：簽名裡**不可以**有陣營。"""
    import inspect

    params = set(inspect.signature(blocks_los).parameters)
    assert not {p for p in params if "faction" in p or "shooter" in p}


# ---- 發數 → 持續時間 ----


def test_more_rounds_means_a_longer_screen() -> None:
    """發數是發煙者**唯一能調的旋鈕**，所以它必須真的有差別。"""
    assert duration_ticks(1) < duration_ticks(4) < duration_ticks(12)
    assert duration_ticks(0) == duration_ticks(1)  # 0/1 發同義，不會回負數


# ---- 接線層 ----


def test_a_smoke_feature_round_trips_through_the_db(session_factory) -> None:  # type: ignore[no-untyped-def]
    """煙存成 `MapFeature` 而不是熱狀態：熱狀態是 unit 鍵值的，硬塞 pseudo-unit
    會讓每一個 `hot.get_all()` 的消費端都得學會忽略它。"""
    from _order_fakes import seed_world

    from app.engine.smoke_wiring import emplace_smoke, load_active_smoke, purge_expired_smoke

    world = seed_world(session_factory)
    db = session_factory()
    cloud = emplace_smoke(
        db, world.session_id, lat=24.0, lng=121.0, tick=5, rounds=3, owner_faction="BLUE"
    )
    db.commit()
    assert cloud.radius_m == DEFAULT_SMOKE_RADIUS_M
    assert cloud.expires_at_tick == 5 + duration_ticks(3)

    assert len(load_active_smoke(db, world.session_id, tick=6)) == 1
    assert load_active_smoke(db, world.session_id, tick=cloud.expires_at_tick) == []

    # 清理是**維護不是正確性**——過期的煙靠 active_at 就已經失效。
    assert purge_expired_smoke(db, world.session_id, tick=cloud.expires_at_tick) == 1
    db.commit()
    assert load_active_smoke(db, world.session_id, tick=6) == []
    db.close()


def test_each_kind_of_broken_smoke_row_is_skipped_not_fatal(session_factory) -> None:  # type: ignore[no-untyped-def]
    """一筆髒資料不該毀掉整局的視線判定。

    ⚠ **每一種壞法各給一列**。我第一版只放了一列同時「幾何壞掉 + 缺到期 tick」，
    於是拿掉任一個 guard 測試都照樣綠（另一個 guard 接住了）——突變測試抓出來的。

    誠實記一筆：把「缺 `expires_at_tick` → None」改成「→ 0」這個突變**殺不掉，
    而且那是對的**——0 代表「已於 tick 0 到期」，`active_at()` 一律判它失效，
    結果完全等價。那是等價突變，不是測試漏洞；硬湊一條測試去分辨它只會製造假保護。
    """
    from _order_fakes import seed_world

    from app.engine.smoke_wiring import SMOKE_KIND, load_active_smoke
    from app.models.tables import MapFeature

    world = seed_world(session_factory)
    db = session_factory()
    broken = [
        {"geometry": [121.0, 24.0], "attributes": {}},  # 幾何好、缺 expires_at_tick
        {"geometry": "not-a-point", "attributes": {"expires_at_tick": 99}},  # 幾何壞
        {"geometry": [121.0], "attributes": {"expires_at_tick": 99}},  # 座標不足
        {"geometry": ["a", "b"], "attributes": {"expires_at_tick": 99}},  # 座標非數值
    ]
    for spec in broken:
        db.add(
            MapFeature(
                session_id=world.session_id,
                kind=SMOKE_KIND,
                geometry_type="POINT",
                owner_faction="BLUE",
                influence_radius_m=100.0,
                **spec,
            )
        )
    db.commit()
    assert load_active_smoke(db, world.session_id, tick=0) == []
    db.close()


def test_radar_sees_through_smoke_but_optics_do_not() -> None:
    """煙擋光學/紅外，**擋不住雷達**——把雷達也擋掉等於把煙幕當成電磁屏障。"""
    from app.intel.sensor import SensorProfile

    optical = SensorProfile.from_base_stats(
        {"sensor_kind": "OPTICAL", "max_range_m": 5000, "detect_curve": [[5000, 0.9]]}
    )
    acoustic = SensorProfile.from_base_stats(
        {"sensor_kind": "ACOUSTIC", "max_range_m": 5000, "detect_curve": [[5000, 0.9]]}
    )
    assert optical.needs_los is True  # → 會被煙擋
    assert acoustic.needs_los is False  # → 不受煙影響
