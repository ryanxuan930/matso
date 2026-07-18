# scenarios/ — 想定包（SPEC_FULL §11）

每個想定 = 一個目錄（或 zip），結構：

```
<name>/
├── scenario.yaml        # 元資料（contracts/scenario.schema.json 驗證）
├── orbat/blue.yaml      # 藍軍戰鬥序列
├── orbat/red.yaml
├── roe.yaml             # ROE + No-Strike List (GeoJSON)
├── msel.yaml            # 事件注入清單
├── weather_script.yaml  # (可選) SYNTHETIC 天氣
└── overrides/           # (可選) mobility matrix / weaponeering 覆寫
```

`examples/` 需包含 3 個官方想定（M7-1 起逐個建立）：
1. `tutorial-platoon/` — 教學用排級小型想定
2. `battalion-defense/` — 營級防禦
3. `joint-defense/` — 聯合防衛大型想定
