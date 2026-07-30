"""WP-C1 驗收條文：**砲兵對 DUG_IN 步兵連射 5 輪——殲滅極慢，但目標射擊效能顯著下降**。

這條是整張卡存在的理由。壓制系統缺席時，砲兵在模型裡只剩「一個效率很差的殺傷工具」；
真實的火力支援多半不是為了殲滅，是為了讓對方抬不起頭。本檔用數字把那句話釘住。

跑法貼著活執行期的實際順序：
`resolve_area_fire`（戰損 + 壓制半徑內的發數）→ `apply_area_suppression` →
每 tick `tick_suppression`（衰減）。姿態走 `set_posture` + 收斂，不直接寫熱狀態。

實測數字記在 `docs/worklog/suppression-posture.md`。
"""

from __future__ import annotations

from app.adjudication.area_fire import AreaTarget, resolve_area_fire
from app.adjudication.suppression import Posture, fire_modifier
from app.adjudication.weapon import WeaponProfile
from app.engine.rng import DeterministicRNG
from app.engine.suppression_wiring import (
    SUPPRESSION_KEY,
    apply_area_suppression,
    read_posture,
    set_posture,
    tick_suppression,
)
from app.state.hot_state import InMemoryHotState

_AIM = (24.0, 121.0)
_ROUNDS_PER_MISSION = 4  # 一個砲兵連的一輪齊放
_MISSIONS = 5  # 驗收條文的「連射 5 輪」
_TICKS_BETWEEN = 2  # 兩輪之間的間隔（1 tick = 1 分鐘）


def _howitzer() -> WeaponProfile:
    """155mm 榴彈砲級：CEP 100 m、殺傷半徑 50 m（壓制半徑因此是 150 m）。"""
    return WeaponProfile.from_base_stats(
        {
            "max_range_m": 20000,
            "ph_by_range_band": [[20000, 0.5]],
            "damage_by_armor_class": {"SOFT": 60.0},
            "pk_by_armor_class": {"SOFT": 0.6},
            "ammo_types": ["HE"],
            "dispersion_cep_m": 100.0,
            "lethal_radius_m": 50.0,
        }
    )


def _company(strength: float, posture: str, lat: float = _AIM[0]) -> AreaTarget:
    """步兵連：滿編 120、佔地半徑 75 m。

    **明確給 `footprint_radius_m`**：面射擊改成「覆蓋率 × 強度」之後，佔地半徑直接決定
    一發彈能涵蓋這支部隊的多少比例。靠預設值的話，日後改預設會讓這裡的期望值悄悄跑掉。
    """
    return AreaTarget(
        unit_id="INF",
        faction="RED",
        lat=lat,
        lng=_AIM[1],
        armor_class="SOFT",
        current_strength=strength,
        authorized_strength=120.0,
        platform_count=120,
        posture=posture,
        footprint_radius_m=75.0,
    )


def _suppression(hot: InMemoryHotState) -> float:
    raw = (hot.get_unit("INF") or {}).get(SUPPRESSION_KEY, 0.0)
    return float(raw) if isinstance(raw, int | float) else 0.0


def _barrage(posture: Posture) -> tuple[float, float]:
    """連射 5 輪。回 (殘餘戰力, **最後一輪落彈當下**的射擊效能修正)。

    效能刻意在最後一輪落下的當下量，不是在其後的間隔之後——驗收問的是
    「被砲擊的時候還打不打得動」，不是「砲停兩分鐘後恢復多少」（那是另一條測試）。
    """
    hot = InMemoryHotState()
    hot.put_unit("INF", {"lat": _AIM[0], "lng": _AIM[1]})
    tick = 0
    if posture is not Posture.MOVING:
        set_posture(hot, "INF", posture, tick=0)
        for tick in range(0, 245):  # DUG_IN 要 4 小時才到位
            tick_suppression(hot, tick)
        assert read_posture(hot.get_unit("INF") or {}).current is posture

    rng = DeterministicRNG(master_seed=20260731, stream_id="area_fire")
    strength, peak = 120.0, 0.0
    for _ in range(_MISSIONS):
        result = resolve_area_fire(
            _howitzer(),
            _AIM,
            [_company(strength, posture.value)],
            rng,
            tick,
            shooter_id="GUN",
            shooter_faction="BLUE",
            rounds=_ROUNDS_PER_MISSION,
        )
        strength -= result.losses.get("INF", 0.0)
        apply_area_suppression(hot, result.suppressed, "ARTILLERY")
        peak = _suppression(hot)
        for _ in range(_TICKS_BETWEEN):
            tick += 1
            tick_suppression(hot, tick)

    return strength, fire_modifier(peak)


def test_five_missions_suppress_far_more_than_they_kill() -> None:
    """驗收條文本體：殲滅極慢（殘餘戰力仍過半），射擊效能卻掉到六成以下。

    兩個數字要一起看才是這張卡的重點——只看戰損會得到「砲兵沒用」的錯誤結論。
    """
    remaining, fire_mod = _barrage(Posture.DUG_IN)
    assert remaining > 60.0, f"殘餘戰力 {remaining:.1f}/120——不該被 5 輪打殘"
    assert fire_mod < 0.6, f"射擊效能修正 {fire_mod:.2f}——壓制沒有顯著影響"


def test_digging_in_actually_pays_off_against_artillery() -> None:
    """同樣 5 輪，掘壕的傷亡必須明顯少於露天。

    **這條曾經是紅的**：`resolve_area_fire` 原本完全不看姿態，於是掘壕只擋得住直射火力
    ——那把「為什麼要挖散兵坑」整個弄反了。工事最該擋的就是砲擊。
    """
    dug_in, _ = _barrage(Posture.DUG_IN)
    exposed, _ = _barrage(Posture.MOVING)
    assert dug_in > exposed
    # DUG_IN 的被命中率修正是 0.5，但**跨 5 輪不會剛好是一半**：
    # 面射擊的損失與**當前**戰力成正比（被打殘的連隊剩下的人才是可能傷亡的人），
    # 於是露天那邊掉得快、後幾輪的絕對損失反而變小，比值會**大於** 0.5。
    # 舊模型用固定的 `cp_per_platform`（與剩餘戰力無關）才會剛好對半。
    ratio = (120.0 - dug_in) / (120.0 - exposed)
    assert 0.5 <= ratio < 0.65, f"掘壕/露天 傷亡比 {ratio:.3f} 不合理"


def test_suppression_lifts_once_the_shelling_stops() -> None:
    """**壓制是可逆的**，那是它與戰損最根本的差別。停火 13 分鐘內要清乾淨。"""
    hot = InMemoryHotState()
    hot.put_unit("INF", {})
    apply_area_suppression(hot, {"INF": _ROUNDS_PER_MISSION}, "ARTILLERY")
    assert _suppression(hot) == 1.0  # 4 發齊放落在陣地上 → 完全趴下

    for tick in range(1, 14):
        tick_suppression(hot, tick)
    assert _suppression(hot) == 0.0


def test_rounds_that_land_near_but_do_not_hurt_still_suppress() -> None:
    """砲彈在旁邊炸開卻沒傷到你，你照樣得趴下——這正是壓制射擊的定義。

    目標放在瞄準點北方 **135 m**：部隊邊緣（佔地半徑 75 m）距爆點 60 m，
    已在殺傷半徑 50 m 之外（**零戰損**），但仍在壓制半徑 150 m 之內。
    要是壓制名單只取 `losses`，這條就會紅。

    ⚠ 距離從 100 m 改成 135 m 是**模型修正的結果**，不是把測試調鬆：
    面射擊改成面對面之後，「落點到單位中心 100 m」代表部隊近側邊緣只距爆點 25 m
    ——那些人本來就該挨炸。要驗「近失彈不傷人」就得真的落在整個部隊之外。
    """
    target = _company(120.0, Posture.MOVING.value, lat=_AIM[0] + 135.0 / 111_320.0)
    result = resolve_area_fire(
        _howitzer(),
        _AIM,
        [target],
        DeterministicRNG(master_seed=1, stream_id="zero_cep"),
        tick=0,
        shooter_id="GUN",
        rounds=1,
        # CEP 照常抽樣，但這裡要的是「確定落在瞄準點」——用 0 散布把隨機性拿掉，
        # 才驗得到「距離 100 m」這件事本身，而不是驗到某顆種子的運氣。
        dispersion_mult=0.0,
    )
    assert result.losses == {}
    assert result.suppressed == {"INF": 1}
