def test_a_march_interval_order_rewrites_the_footprint() -> None:
    """`column_spacing_km` 要真的改到面射擊讀的那個鍵——否則又是一個「存得進去沒效果」。"""
    from app.engine.formation_wiring import FOOTPRINT_KEY, set_formation
    from app.state.hot_state import InMemoryHotState

    hot = InMemoryHotState()
    hot.put_unit("u1", {"platform_count": 30, FOOTPRINT_KEY: 120.0})

    set_formation(hot, "u1", column_spacing_km=2.0)

    assert (hot.get_unit("u1") or {})[FOOTPRINT_KEY] > 120.0


def test_declaring_only_mounted_leaves_the_footprint_alone() -> None:
    """None＝不動該欄（同 formation/mounted 的紀律）。"""
    from app.engine.formation_wiring import FOOTPRINT_KEY, set_formation
    from app.state.hot_state import InMemoryHotState

    hot = InMemoryHotState()
    hot.put_unit("u1", {"platform_count": 30, FOOTPRINT_KEY: 120.0})

    set_formation(hot, "u1", mounted=True)

    assert (hot.get_unit("u1") or {})[FOOTPRINT_KEY] == 120.0
