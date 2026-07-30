"""裝備 base_stats 的欄位**真的有人讀**——「有值卻被靜默忽略」比死欄位更糟。

## 這一檔在防什麼

2026-07-30 的欄位漂移掃描（99 個契約欄位）找出 6 個「有值卻被靜默忽略」的欄位。
它們的共同形狀是：**schema 綠、roundtrip 綠、所有測試綠，但模擬結果是錯的**——
因為沒有任何測試檢查「這個值有沒有真的走到引擎」。

路徑層級的契約閘門（`test_contract_conformance.py`）看的是端點在不在，
看不到欄位有沒有消費者。這一檔補的是那一層。
"""

from __future__ import annotations

import json
import pathlib

from jsonschema import Draft202012Validator

from app.adjudication.armor import armor_class_from_stats
from app.adjudication.seed_weapons import SEED_LOGISTICS, SEED_VEHICLES
from app.engine.comms import _profile_from_stats
from app.intel.seed_sensors import SEED_SENSORS
from app.movement.mobility import mobility_from_stats

_SCHEMA = pathlib.Path(__file__).resolve().parents[3] / "contracts" / "weaponeering.schema.json"


def _validator(defname: str) -> Draft202012Validator:
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    return Draft202012Validator({"$defs": schema["$defs"], "$ref": f"#/$defs/{defname}"})


def test_seed_logistics_conforms_to_schema() -> None:
    """**這條測試以前不存在，那正是它能漂移的原因。**

    其他每個種子集都有一條（kinetic/artillery/sensor/vehicle），只有 LOGISTICS 沒有。
    它曾經把整包內容多包一層 `"logistics"` 鍵——一有這條測試就會立刻紅。
    """
    validator = _validator("logistics")
    for name, stats in SEED_LOGISTICS.items():
        errors = sorted(validator.iter_errors(stats), key=str)
        assert not errors, f"{name} 不符 logistics $def：{errors}"


def test_logistics_seed_is_not_double_wrapped() -> None:
    """種子不可以再包一層 `logistics` 鍵——`base_stats` **就是** `$defs.logistics` 本身。

    多包一層的後果是兩個靜默錯誤：補給車的 `mobility` 讀不到（被判徒步、不燒油），
    以及與軍械庫 UI 寫入的形狀不一致。
    """
    for name, stats in SEED_LOGISTICS.items():
        assert "logistics" not in stats, f"{name} 多包了一層 logistics 鍵"


def test_supply_trucks_can_actually_self_move_and_burn_fuel() -> None:
    """補給車要**自走且燒油**——這是 `mobility` 位置正確與否的行為級證據。

    `mobility_from_stats` 只讀頂層 `base_stats["mobility"]`。種子若把它包進 `logistics`，
    這裡就會退回 FOOT：補給車用徒步速度移動、而且 `fuel_burn_per_km` 永遠讀不到，
    #84 的油料機制對它完全無效。
    """
    stats = SEED_LOGISTICS["FUEL_TRUCK"]
    mob = mobility_from_stats([stats])
    assert mob.profile == "WHEELED", f"補給車應為輪型自走，實際 {mob.profile}"
    assert mob.fuel_burn_per_km > 0, "補給車必須燒油，否則 #84 的油料機制對它無效"


def test_armor_class_comes_from_equipment_not_from_thin_air() -> None:
    """主戰車就是 ARMOR——**資料一直都在裝備範本上**，缺的只是導出這一步。

    過去引擎讀 `TacticalUnit.attributes["armor_class"]`，但沒有任何想定 schema 定義它、
    loader 也從不寫 attributes，於是每個單位都退回 INFANTRY，主戰車被步槍以 pk=0.70 打死。
    """
    assert armor_class_from_stats([SEED_VEHICLES["MBT"]]) == "ARMOR"
    assert armor_class_from_stats([SEED_VEHICLES["IFV_TRACKED"]]) == "LIGHT_VEHICLE"
    # 無編裝 → 步兵（中性預設，既有純步兵單位行為不變）
    assert armor_class_from_stats([]) == "INFANTRY"
    # 混編取最強：帶主戰車的單位不會因為同時編有輕型載具而被當成輕裝甲
    assert armor_class_from_stats([SEED_VEHICLES["IFV_TRACKED"], SEED_VEHICLES["MBT"]]) == "ARMOR"
    # 髒資料不得讓單位變成無敵
    assert armor_class_from_stats([{"armor_class": "SUPER_TANK"}]) == "INFANTRY"


def test_antenna_gain_uses_the_contract_field_name() -> None:
    """契約與軍械庫 UI 寫的是 `antenna_gain_dbi`（差一個 `i`）。

    引擎過去只讀 `antenna_gain_db`，於是**經 UI 建立的每一組通信裝備，
    天線增益都被靜默忽略、永遠吃預設值**。
    """
    assert _profile_from_stats({"antenna_gain_dbi": 11.0}).antenna_gain_db == 11.0
    # 舊名保留為退路（手寫/既存資料）
    assert _profile_from_stats({"antenna_gain_db": 7.0}).antenna_gain_db == 7.0
    # 兩個都沒有 → 預設，且**不可以是 0**（0 會讓鏈路預算算出錯誤的可通聯範圍）
    assert _profile_from_stats({}).antenna_gain_db > 0


def test_night_capable_is_supplied_by_seeds_and_matches_physics() -> None:
    """`SensorProfile` 讀 `night_capable`，但過去**契約/種子/前端全都沒有供應**
    ——等於全系統沒有夜視感測器。

    指派要合物理：熱像看溫差、雷達用電波、聲學聽聲音，都不靠可見光；日間光學才吃夜間懲罰。
    """
    validator = _validator("sensor")
    for name, stats in SEED_SENSORS.items():
        assert not sorted(validator.iter_errors(stats), key=str), f"{name} 不符 sensor $def"
        assert "night_capable" in stats, f"{name} 沒有供應 night_capable"
    assert SEED_SENSORS["EO_DAY"]["night_capable"] is False
    for name in ("IR_THERMAL", "GROUND_RADAR", "ACOUSTIC_ARRAY"):
        assert SEED_SENSORS[name]["night_capable"] is True, f"{name} 不該吃夜間懲罰"


def test_rounds_per_mission_and_emplace_ticks_reach_the_profile() -> None:
    """兩個過去**完全沒被解析**的欄位要進得了 `WeaponProfile`。

    `WeaponProfile.from_base_stats` 是白名單式逐欄建構的，所以「契約有、種子有、
    UI 編得動」完全不代表引擎讀得到——這兩欄就這樣躺了很久。
    """
    from app.adjudication.seed_weapons import SEED_ARTILLERY
    from app.adjudication.weapon import WeaponProfile

    for name, stats in SEED_ARTILLERY.items():
        p = WeaponProfile.from_base_stats(stats)
        if "rounds_per_mission" in stats:
            assert p.rounds_per_mission == stats["rounds_per_mission"], name
        if "emplace_ticks" in stats:
            assert p.emplace_ticks == stats["emplace_ticks"], name
    # 未宣告 → 0（中性預設：不設限 / 面板沿用自己的預設）
    bare = WeaponProfile.from_base_stats(
        {
            "max_range_m": 1000,
            "ph_by_range_band": [[1000, 0.5]],
            "damage_by_armor_class": {"INFANTRY": 10},
            "ammo_types": ["A"],
        }
    )
    assert bare.rounds_per_mission == 0
    assert bare.emplace_ticks == 0


def test_emplacement_gate_is_neutral_when_the_unit_never_moved() -> None:
    """**缺 `arrived_at_tick` ＝視為早已就位**，不是「剛抵達」。

    這是整條閘門最重要的一條：從未移動過的單位、以及這張卡之前就存在的所有 session，
    熱狀態裡都沒有這個鍵。若把缺鍵讀成「剛到」，既有想定的砲兵會全部突然打不出去。
    """
    from types import SimpleNamespace

    from app.engine.clock import SimTime
    from app.engine.fire_wiring import AreaFireAdjudicator

    now = SimTime(tick=100, sim_time_ms=0)
    entry = SimpleNamespace(profile=SimpleNamespace(emplace_ticks=5))

    # 缺鍵 → 放行
    assert AreaFireAdjudicator._emplacement_block(entry, {}, now) is None
    # 剛抵達（差 1 tick）→ 擋
    blocked = AreaFireAdjudicator._emplacement_block(entry, {"arrived_at_tick": 99}, now)
    assert blocked is not None and blocked[0] == "NOT_EMPLACED"
    # 等滿了 → 放行
    assert AreaFireAdjudicator._emplacement_block(entry, {"arrived_at_tick": 95}, now) is None

    # 武器未宣告 emplace_ticks → **連熱狀態都不讀就放行**。
    # 用會爆的 dict 才測得出「早退」：只斷言回 None 的話，`wait <= 0` 改成 `wait < 0`
    # 也會通過（wait=0 時 elapsed>=0 恆成立，兩者結果相同）——那是等價突變，測不到。
    class _ExplodingState(dict):  # type: ignore[type-arg]
        def get(self, *_a: object, **_k: object) -> object:
            raise AssertionError("未宣告 emplace_ticks 時不該讀熱狀態")

    off = SimpleNamespace(profile=SimpleNamespace(emplace_ticks=0))
    assert AreaFireAdjudicator._emplacement_block(off, _ExplodingState(), now) is None
