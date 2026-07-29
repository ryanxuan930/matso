# scenarios/ — 想定包（SPEC_FULL §11）

每個想定 = 一個目錄（或 zip），結構：

```
<name>/
├── scenario.yaml        # 元資料（contracts/scenario.schema.json 驗證）
├── orbat/blue.yaml      # 藍軍戰鬥序列
├── orbat/red.yaml
├── roe.yaml             # 交戰規則（No-Strike 清單在 scenario.yaml 的 no_strike_zones）
├── msel.yaml            # 事件注入清單
├── weather_script.yaml  # (可選) SYNTHETIC 天氣
└── overrides/           # (可選) mobility matrix / weaponeering 覆寫
```

`examples/` 的 3 個官方想定（WP-B6 補齊）：
1. `tutorial-platoon/` — 教學用排級小型想定（5 單位 / 花蓮隘口）
2. `battalion-defense/` — 營級防禦（27 單位 / 大漢溪—石門隘口；機步營守 vs 裝甲營攻）
3. `joint-defense/` — 聯合防衛大型想定（29 單位 / 高雄西南沿海；BLUE+GREEN 盟軍 vs RED）

新增官方想定時**不需要**改測試——`core/tests/unit/test_scenario_roundtrip.py` 會掃描本目錄
自動納入「無損 + 位元一致」驗收。
