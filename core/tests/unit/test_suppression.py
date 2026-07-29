"""壓制與姿態（WP-C1）——純函數。

**這張卡存在的理由**：`shooter_suppression_modifier` / `target_posture_modifier`
從交戰真實化時代就恆為 1.0——掛點早就留好，系統一直缺席。
沒有壓制，砲兵的主要戰術功能（讓對方抬不起頭，而非殲滅）表現不出來。
"""

from __future__ import annotations

from app.adjudication.suppression import (
    Posture,
    PostureState,
    add_suppression,
    decay_suppression,
    fire_modifier,
    move_modifier,
    posture_modifier,
)

# ---- 中性預設（既有局與 golden 位元不變）----


def test_no_suppression_is_exactly_one() -> None:
    """**位元不變**：無壓制 → 修正剛好 1.0，既有局的命中率完全不動。"""
    assert fire_modifier(0.0) == 1.0
    assert move_modifier(0.0) == 1.0


def test_moving_posture_is_exactly_one() -> None:
    assert posture_modifier(Posture.MOVING) == 1.0


# ---- 累積與衰減 ----


def test_artillery_suppresses_far_more_than_rifles() -> None:
    """砲兵用來壓制、步槍用來殺傷——這個差別就是本卡要模型化的東西。"""
    assert add_suppression(0.0, "ARTILLERY") > add_suppression(0.0, "KINETIC") * 3


def test_suppression_is_capped() -> None:
    s = 0.0
    for _ in range(20):
        s = add_suppression(s, "ARTILLERY")
    assert s == 1.0


def test_suppression_recovers_after_ceasefire() -> None:
    """**壓制是可逆的**——那是它與戰損最根本的差別。

    1 tick = 1 分鐘，滿壓制約 13 分鐘完全恢復（半衰期約 2 分鐘）。
    數字寫在這裡是為了讓「調了係數但沒想清楚時間尺度」當場紅燈。
    """
    s = 1.0
    for _ in range(6):
        s = decay_suppression(s)
    assert 0.05 < s < 0.2, "半衰期跑掉了：6 分鐘後應該還剩一成多"
    for _ in range(7):
        s = decay_suppression(s)
    assert s == 0.0, "13 分鐘後應該完全恢復"


def test_decay_snaps_to_zero_instead_of_trailing_forever() -> None:
    """留一個除不盡的小數會讓熱狀態每 tick 都在變，於是每 tick 推一次 STATE_DIFF 給所有 client。"""
    assert decay_suppression(0.011) == 0.0


def test_full_suppression_hurts_but_does_not_disable() -> None:
    """滿壓制不是「不能開槍」——趴著還是打得出去，只是打不準。"""
    assert 0.0 < fire_modifier(1.0) < 0.5
    assert 0.0 < move_modifier(1.0) < 1.0


# ---- 姿態轉換 ----


def test_hasty_is_immediate_but_dug_in_takes_hours() -> None:
    st = PostureState()
    hasty = st.order(Posture.HASTY, tick=0)
    assert hasty.settled(0) is Posture.HASTY

    dug = st.order(Posture.DUG_IN, tick=0)
    assert dug.settled(0) is Posture.MOVING, "宣告掘壕的那一秒就享有掘壕防護＝免費按鈕"
    assert dug.settled(239) is Posture.MOVING
    assert dug.settled(240) is Posture.DUG_IN


def test_partial_progress_gives_the_previous_level_not_a_blend() -> None:
    """挖到一半就是還沒挖好。中間值會讓「已就位」這件事變得沒有意義。"""
    st = PostureState(current=Posture.HASTY, target=Posture.DEFENSE, since_tick=0)
    assert st.settled(29) is Posture.HASTY


def test_repeating_the_same_order_does_not_reset_the_clock() -> None:
    """反覆下同一道令會讓工事永遠挖不完——那是最容易寫出來的 bug。"""
    st = PostureState().order(Posture.DUG_IN, tick=0)
    again = st.order(Posture.DUG_IN, tick=100)
    assert again.since_tick == 0
    assert again.settled(240) is Posture.DUG_IN


def test_moving_wipes_the_entrenchment() -> None:
    """挖到一半的洞帶不走。"""
    st = PostureState(current=Posture.DUG_IN, target=Posture.DUG_IN, since_tick=0)
    assert st.interrupted(500).settled(500) is Posture.MOVING


def test_advance_settles_the_transition() -> None:
    st = PostureState().order(Posture.DEFENSE, tick=10)
    assert st.advance(20).current is Posture.MOVING  # 還沒到
    settled = st.advance(40)
    assert settled.current is Posture.DEFENSE and settled.target is Posture.DEFENSE


def test_dug_in_halves_incoming_hit_chance() -> None:
    """掘壕的生存優勢——沒有這個，防禦方的準備工作在模型裡毫無意義。"""
    assert posture_modifier(Posture.DUG_IN) == 0.5
    assert posture_modifier(Posture.DEFENSE) < posture_modifier(Posture.HASTY) < 1.0
