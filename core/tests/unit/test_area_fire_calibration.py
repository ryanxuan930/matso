"""面射擊的**絕對殺傷量**校準（WP-C10.2）。

## 為什麼需要這一檔

舊模型有兩個問題，一個是係數、一個是結構：

1. **CEP 遠大於致命半徑**：155mm 的 `dispersion_cep_m=150` vs `lethal_radius_m=60`，
   Rayleigh 抽樣下只有約 10.5% 的彈落在致命半徑內，再乘線性遞減 → 每發只發揮名目
   `pk` 的約 3.6%。
2. **目標被當成一個點**：一個連散在 200–400m 正面，但舊的 `_loss_for` 量的是
   「落點到單位座標」——落在連隊區域內、離中心 100m 的彈算 0 傷害。
   **這是模型結構錯誤，不是係數偏低。**

## 校準錨點（⚠ 假設值，不是量測值）

**一個 155mm 砲兵連 18 發（6 門 ×3 發）打露天步兵連 → 約 15% 傷亡**，
落在「壓制」（10–30% 傷亡）的中段。傷亡與 `pk` 是**嚴格線性**的，
要改錨點只需等比例調整 `seed_weapons.py` 的 `pk_by_armor_class`。

這一檔把錨點寫成可執行的斷言：改了公式或係數而沒有一起改錨點，就會紅。
"""

from __future__ import annotations

import pytest

from app.adjudication.area_fire import AreaTarget, footprint_for, resolve_area_fire
from app.adjudication.seed_weapons import SEED_ARTILLERY
from app.adjudication.weapon import WeaponProfile
from app.engine.rng import DeterministicRNG

_AIM = (24.0, 121.0)
_STRENGTH = 100.0


def _fire(level: str, rounds: int, posture: str = "MOVING", seed: int = 7) -> float:
    """對該編制打 `rounds` 發 155mm，回傷亡百分比。瞄準點＝單位中心。"""
    weapon = WeaponProfile.from_base_stats(SEED_ARTILLERY["HOWITZER_155_SP"])
    target = AreaTarget(
        unit_id="T",
        faction="RED",
        lat=_AIM[0],
        lng=_AIM[1],
        armor_class="INFANTRY",
        current_strength=_STRENGTH,
        authorized_strength=_STRENGTH,
        posture=posture,
        footprint_radius_m=footprint_for(level),
    )
    result = resolve_area_fire(
        weapon,
        _AIM,
        [target],
        DeterministicRNG(master_seed=seed, stream_id="calib"),
        tick=0,
        shooter_id="GUN",
        rounds=rounds,
    )
    return result.losses.get("T", 0.0) / _STRENGTH * 100.0


def test_a_battery_mission_neutralises_a_company_in_the_open() -> None:
    """**校準錨點**：18 發 155mm 打露天步兵連 → 落在壓制帶（10–30%）。

    這條紅了代表校準跑掉了——不是把區間放寬，是回去看公式或 pk 哪裡變了。
    """
    pct = _fire("COMPANY", rounds=18)
    assert 10.0 <= pct <= 30.0, f"18 發打露天連造成 {pct:.1f}% 傷亡，不在壓制帶"


def test_lethality_falls_off_sharply_with_unit_size() -> None:
    """同樣 18 發，單位越大越不痛——**這是面射擊的本質**。

    舊的點目標模型做不出這個關係：不論連或旅，量的都是「到中心點的距離」。
    """
    fireteam = _fire("FIRETEAM", rounds=18)
    company = _fire("COMPANY", rounds=18)
    battalion = _fire("BATTALION", rounds=18)
    brigade = _fire("BRIGADE", rounds=18)
    assert fireteam > company > battalion > brigade
    # 旅級被一個砲兵連打 18 發應該幾乎沒感覺
    assert brigade < 3.0, f"旅級承受 {brigade:.1f}%——一個砲兵連不該打得動一個旅"
    # 伍級被同一輪打則應該很慘
    assert fireteam > 30.0, f"伍級只承受 {fireteam:.1f}%——18 發 155mm 該把一個伍打散"


def test_digging_in_roughly_halves_the_casualties() -> None:
    """掘壕的防護要真的看得到（`posture_modifier` DUG_IN = 0.5）。"""
    exposed = _fire("COMPANY", rounds=18, posture="MOVING")
    dug_in = _fire("COMPANY", rounds=18, posture="DUG_IN")
    assert dug_in < exposed
    ratio = dug_in / exposed
    assert 0.45 <= ratio <= 0.55, f"掘壕/露天 = {ratio:.3f}，與 0.5 的修正對不上"


def test_casualties_scale_linearly_with_round_count() -> None:
    """發數加倍、傷亡大致加倍（未觸及戰力上限時）。

    這個性質讓「要改校準只需等比例調 pk」這句話成立——沒有它，
    錨點就沒辦法用一個乘數搬動。
    """
    a = _fire("COMPANY", rounds=9)
    b = _fire("COMPANY", rounds=18)
    assert b == pytest.approx(a * 2, rel=0.35), f"9 發 {a:.2f}% vs 18 發 {b:.2f}% 非線性"
