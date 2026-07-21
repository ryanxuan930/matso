# MATSO — Military Analysis & Tactical Simulation Orchestrator
# 完整系統規格書（SPEC_FULL）V1.0

> 本文件由 SPEC.md V4.0 擴充而成，為 MATSO 的權威規格（Source of Truth）。
> 開發流程、任務拆解與工程規範請見 [HOW_TO.md](HOW_TO.md)。
> 文獻依據請見《AI Command Staff Architecture and Domain-Specific Training.md》。
>
> **語言慣例**：本文以正體中文撰寫，所有程式識別字、API 欄位、協定名稱一律使用英文。
> **關鍵字慣例**：MUST（必須）、SHOULD（應該）、MAY（可以）依 RFC 2119 解釋。

---

## 目錄

1. [系統使命與設計原則](#1-系統使命與設計原則)
2. [整體架構](#2-整體架構)
3. [模擬核心引擎（Simulation Kernel）](#3-模擬核心引擎simulation-kernel)
4. [地理空間模組（Terrain Module）](#4-地理空間模組terrain-module)
5. [氣象環境模組（Weather Module）](#5-氣象環境模組weather-module)
6. [通訊與電磁模組（Comms/EW Module）](#6-通訊與電磁模組commsew-module)
7. [戰鬥裁決引擎（Adjudication Engine）](#7-戰鬥裁決引擎adjudication-engine)
8. [後勤與持續戰力模型（Logistics Module）](#8-後勤與持續戰力模型logistics-module)
9. [AI 指揮參謀子系統（AI Subsystem）](#9-ai-指揮參謀子系統ai-subsystem)
10. [反幻覺與合規護欄（Guardrails）](#10-反幻覺與合規護欄guardrails)
11. [想定管理系統（Scenario Management）](#11-想定管理系統scenario-management)
12. [使用者角色與權限（RBAC）](#12-使用者角色與權限rbac)
13. [前端與共同作戰圖像（COP / Frontend）](#13-前端與共同作戰圖像cop--frontend)
14. [行動後檢討系統（AAR System)](#14-行動後檢討系統aar-system)
15. [資料庫架構（Database Schema）](#15-資料庫架構database-schema)
16. [API 與通訊協定契約](#16-api-與通訊協定契約)
17. [插件系統規格（Plugin System）](#17-插件系統規格plugin-system)
18. [非功能需求（NFR）](#18-非功能需求nfr)
19. [測試與驗證策略](#19-測試與驗證策略)
20. [部署架構](#20-部署架構)
21. [開發路線圖（Roadmap）](#21-開發路線圖roadmap)

---

## 1. 系統使命與設計原則

### 1.1 使命陳述

MATSO 是一套 AI 輔助的兵棋推演與戰術決策支援平台，目的在於：

1. **以模擬代替實戰風險**：在虛擬環境中演練防衛想定，降低決策錯誤在真實世界的代價。
2. **訓練與教育**：提供軍事院校、國防智庫與學術單位可重複、可量測、可檢討的推演環境。
3. **決策支援研究**：作為 Neuro-Symbolic AI（神經符號混合架構）在高風險決策領域的研究平台。

### 1.2 五大設計原則（All modules MUST comply）

| # | 原則 | 具體規範 |
|---|------|----------|
| P1 | **決定論與機率推理嚴格分離**（Neuro-Symbolic Separation） | 一切物理計算（射程、視線、地形、天氣、傷害）由確定性 Python 引擎負責；LLM 只負責戰術推理與敘事，**永遠不裁決物理事實**。 |
| P2 | **人在迴路（Human-in-the-Loop）** | AI 產出皆為「建議」，白軍（White Cell）與各軍指揮官擁有最終否決權。系統不得自動執行未經人類確認的破壞性決策。 |
| P3 | **國際人道法（IHL）與交戰規則（ROE)內建合規** | 所有 AI 裁決輸出 MUST 附帶思維鏈（CoT），並通過獨立合規檢查模組後才能進入事件帳本。 |
| P4 | **可重現性（Determinism & Replay）** | 相同想定 + 相同種子 + 相同指令序列 MUST 產生 bit-identical 的模擬結果。所有隨機性來自受控的 seeded RNG。 |
| P5 | **模組化熱插拔（Plug-and-Play）** | 所有輔助功能（氣象、地形、EW、AI 角色）為獨立模組，透過標準契約（REST/gRPC + JSON Schema）接入 Core Orchestrator，可獨立替換、獨立測試、獨立部署。 |

### 1.3 名詞定義

| 術語 | 定義 |
|------|------|
| Tick | 模擬時間的最小推進單位，預設 1000ms 模擬時間（可設定），與真實牆鐘時間解耦 |
| Session | 一場推演的完整生命週期（建立 → 執行 → 結束 → AAR） |
| Scenario | 想定：初始兵力部署（ORBAT）、地圖範圍、ROE、勝利條件、事件注入清單（MSEL）的組合 |
| Order | 玩家或 AI 對單位下達的指令（移動、接戰、偵察、補給…） |
| Adjudication | 裁決：由確定性引擎針對事件（交戰、偵測、通訊）計算結果 |
| White Cell | 白軍／統裁部：主持推演、注入事件、仲裁爭議的導演角色 |
| COP | Common Operational Picture，共同作戰圖像 |
| AAR | After Action Review，行動後檢討 |
| MSEL | Master Scenario Events List，主想定事件清單 |
| ORBAT | Order of Battle，戰鬥序列 |

---

## 2. 整體架構

### 2.1 架構總覽

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Nuxt 4 Frontend (platform/)                  │
│   COP Map│Instruction UI│White Cell│AAR Dashboard│Scenario Editor   │
└──────────────┬───────────────────────────────┬──────────────────────┘
               │ REST (Instruction/Query)      │ WebSocket (Status Updates)
┌──────────────▼───────────────────────────────▼──────────────────────┐
│                  Core Orchestrator (FastAPI, core/)                 │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌────────────┐  │
│  │ Sim Kernel   │ │ Adjudication │ │ Order        │ │ Plugin     │  │
│  │ (tick loop)  │ │ Engine       │ │ Validator    │ │ Registry   │  │
│  └──────────────┘ └──────────────┘ └──────────────┘ └────────────┘  │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐                 │
│  │ Event Ledger │ │ State Store  │ │ Guardrail    │                 │
│  │ (MariaDB)    │ │ (Redis)      │ │ Gateway      │                 │
│  └──────────────┘ └──────────────┘ └──────────────┘                 │
└───┬──────────────┬──────────────┬──────────────┬────────────────────┘
    │ gRPC/REST    │ REST         │ REST         │ OpenAI-compatible API
┌───▼──────┐ ┌─────▼──────┐ ┌─────▼──────┐ ┌─────▼──────────────────┐
│ Terrain  │ │ Weather    │ │ Vision     │ │ AI Node (vLLM, 31B)    │
│ Module   │ │ Module     │ │ Arbiter    │ │ Role Manager +         │
│ (DTED/   │ │ (CWA data) │ │(CV, Not AI)│ │ LoRA hot-swap          │
│  hexgrid)│ │            │ │            │ │ (Phase 2: MoA cluster) │
└──────────┘ └────────────┘ └────────────┘ └────────────────────────┘
```

### 2.2 技術選型（Locked-in decisions）

| 層 | 技術 | 理由 |
|----|------|------|
| 前端 | Nuxt 4 + Vue 3 + TypeScript | 已建立於 `platform/` |
| 地圖 | MapLibre GL JS + deck.gl | 開源、可離線部署（自建 tile server）、支援大量單位渲染 |
| 軍隊符號 | milsymbol (MIL-STD-2525D) | 標準化單位符號產生 |
| 後端 | Python 3.12 + FastAPI + Pydantic v2 | 契約先行、型別安全 |
| ORM / DB | Prisma（schema 權威）+ MariaDB 11 | 沿用 SPEC.md 既定；Python 端使用 SQLAlchemy 讀寫同一 schema（見 §15.4） |
| 快取 / PubSub | Redis 7（state cache + pub/sub） | tick 廣播與熱狀態 |
| 地理運算 | rasterio + GDAL + numpy + h3-py | DTED 解析與六角網格 |
| AI 推論 | vLLM（OpenAI-compatible endpoint）+ LoRA hot-swap | SPEC.md 既定 |
| 向量庫 | Qdrant | RAG/RAFT 檢索，可離線部署 |
| 部署 | Docker Compose（Phase 1）→ K8s（Phase 2） | 單節點起步 |
| 觀測 | Prometheus + Grafana + structlog | NFR 需求 |

### 2.3 資料流（一次完整的指令生命週期）

```
玩家下令 → REST POST /orders
  → [1] Order Validator：語法/權限/單位存在性檢查
  → [2] Physics Pre-check（同步、<50ms）：
        Terrain Module 查詢 LOS、射程、地形可通行性
        ├─ 不可行 → 立即 REJECTED（AI 永不介入，杜絕物理幻覺）
        └─ 可行 → 進入 pending queue
  → [3] 下一個 tick 開始時，Sim Kernel 取出 pending orders
  → [4] Adjudication Engine 確定性計算結果（含天氣/地形修正）
  → [5] 需要戰術判斷的事件（如 OPFOR 反應）→ AI Node（非同步，不阻塞 tick）
  → [6] Guardrail Gateway 驗證 AI 輸出（schema + CoT + IHL）
  → [7] 寫入 Event Ledger（不可變）→ Redis 更新熱狀態
  → [8] WebSocket 依 faction 視野過濾後推播給各方前端
```

**關鍵時序約束**：
- 步驟 [2] 的物理預檢 MUST 同步完成（p99 < 50ms），失敗即拒絕。
- 步驟 [5] 的 AI 呼叫 MUST 非同步，不得阻塞 tick loop；AI 逾時（預設 30s）時 Kernel 採用 doctrine-based fallback（規則表驅動的預設行為）。

---

## 3. 模擬核心引擎（Simulation Kernel）

### 3.1 時間模型

- **Tick-based discrete simulation**：模擬時間以 tick 推進，`simTickRateMs` 定義一個 tick 代表的模擬毫秒數（預設 1000ms）。
- **時間壓縮（Time Compression）**：牆鐘與模擬時間比例可由 White Cell 動態調整：`0x（暫停）/ 1x / 2x / 5x / 10x / 60x`。調整事件 MUST 記入 Event Ledger。
- **階段制支援（Turn-based mode）**：除連續模式外，MUST 支援回合制（IGO-UGO 與 WEGO 兩種）供教學型推演使用。模式在 Scenario 中宣告，Session 進行中不可切換。
- **禁止牆鐘依賴**：模擬邏輯內 MUST NOT 呼叫 `datetime.now()` / `time.time()`；一切時間取自 `SimClock` 服務（可測試、可重播）。

### 3.2 決定性與隨機數

- 每個 Session 有一個 64-bit master seed（記錄於 DB）。
- 所有隨機抽樣透過 `DeterministicRNG(master_seed, stream_id)`：不同子系統（裁決、偵測、通訊）使用獨立 stream，避免跨系統耦合。
- **Golden Replay 保證**：`replay(session_id)` 讀取 Event Ledger 中的指令序列重新執行，最終狀態 hash MUST 與原始執行一致（CI 強制驗證，見 §19）。**分階段落地**：Phase 1（M1，orders 尚不存在）以「合成想定 + seed 決定性」驗證重播機制；ledger 指令序列重播想定於 O3.1（order pipeline）完成後接入同一 harness（見 TASKS O3.1）。

### 3.3 Tick Loop 規格

```python
# 虛擬碼：core/app/engine/kernel.py
async def tick(self):
    t0 = perf_counter()
    orders = await self.order_queue.drain()          # 取出本 tick 待處理指令
    for order in orders:
        events = self.adjudicator.resolve(order)     # 確定性裁決
        await self.ledger.append(events)             # 不可變寫入
    await self.movement.step()                       # 單位移動推進
    await self.sensors.sweep()                       # 偵測掃描 → 產生 detection events
    await self.comms.evaluate()                      # 通訊狀態重算
    await self.logistics.consume()                   # 補給消耗
    self.check_triggers()                            # MSEL 觸發器 & 勝利條件
    await self.broadcaster.publish(self.diff())      # 增量狀態推播
    assert perf_counter() - t0 < self.tick_budget    # NFR: 見 §18
```

**Tick 預算**：500 個作戰單位規模下，單一 tick 的計算 MUST 在 200ms（p99）內完成。超出預算時 Kernel 記錄 `TICK_OVERRUN` 事件並自動降頻，MUST NOT 靜默丟事件。

### 3.4 狀態管理與快照

- **熱狀態**：全部單位當前狀態存於 Redis（key: `session:{id}:unit:{id}`），Kernel 為唯一寫入者（single-writer principle）。
- **檢查點（Checkpoint）**：每 N ticks（預設 300）將完整狀態序列化寫入 MariaDB `SimCheckpoint` 表。White Cell 可將 Session 回滾（rollback）至任一檢查點——回滾本身也是 Ledger 事件。
- **崩潰復原**：Orchestrator 重啟後 MUST 能由「最近檢查點 + 之後的 Ledger 事件重播」恢復至崩潰前狀態。

---

## 4. 地理空間模組（Terrain Module）

獨立服務 `modules/terrain/`，gRPC + REST 雙介面。

### 4.1 資料來源

- **輸入**：`TW_ALL.tiff`（臺灣 DTED，WGS84 / EPSG:4326）。**檔案路徑 MUST 可由環境變數
  `MATSO_DTED_PATH` 指定**（真檔數 GB，常置於外接硬碟；開發/CI 以合成夾具替代，見 HOW_TO §2.3）。
  PAMDataset metadata：
  - Min elevation: `-3.0099999904633` m（沿海／低於海平面）
  - Max elevation: `3691.3601074219` m（高山作戰）
  - Mean: `754.01758094214` m、Valid data: `34.99%`（其餘為海域 nodata → 視為海面 0m，標記 `water=true`）
- 啟動時以 rasterio 載入並建立 memory-mapped overview 金字塔，冷啟動 MUST < 30s。

### 4.2 六角網格（Hexagonal Grid）

- 參照 Geo-Commander 框架，將連續 raster 轉譯為六角網格供 AI 與裁決引擎使用。
- 使用 **H3 resolution 8**（約 0.74 km² / cell，邊長約 461m）為戰術預設；Scenario 可宣告 res 7–9。
- 每個 cell 預計算並快取：`{h3_index, center_latlng, elevation_mean, elevation_max, slope_deg, terrain_class, water, mobility_cost}`。
- `terrain_class` 分類：`URBAN, FOREST, GRASSLAND, WETLAND, BARREN, WATER, MOUNTAIN`（Phase 1 由坡度+高程規則推導；Phase 2 接入國土利用調查圖層插件）。

### 4.3 服務 API（契約見 `contracts/terrain.proto`）

| RPC | 輸入 | 輸出 | SLA |
|-----|------|------|-----|
| `GetElevation` | lat, lng | elevation_m, water | p99 < 5ms |
| `CheckLOS` | observer{lat,lng,height_agl}, target{...} | visible, obstruction_point, fresnel_clearance | p99 < 20ms |
| `GetPath` | from_h3, to_h3, mobility_profile | ordered h3 list, total_cost, eta_ticks | p99 < 100ms |
| `GetCellBatch` | list[h3_index] | list[CellInfo] | p99 < 20ms |
| `GetViewshed` | observer, radius_m | list[visible h3] | p99 < 200ms |

- **LOS 演算法**：沿大圓線以 30m 步長取樣 DTED，考慮地球曲率（4/3 等效半徑，供 RF 用）與觀測者/目標高度（AGL）。
- **路徑規劃**：A* on hex grid；`mobility_profile`（`FOOT, WHEELED, TRACKED, BOAT, AIR`）決定各 terrain_class 的通行成本矩陣（定義於 `contracts/mobility_matrix.json`，屬想定可覆寫資料）。

### 4.4 快取策略

- Viewshed 與 Path 結果以 `(args_hash)` 為 key 快取於 Redis，TTL 至地形變更事件（工兵爆破、橋樑摧毀等 `TERRAIN_MODIFIED` 事件會 invalidate 對應區域）。

---

## 5. 氣象環境模組（Weather Module）

獨立微服務 `modules/weather/`，可熱插拔（P5）。

### 5.1 資料來源與模式

| 模式 | 說明 |
|------|------|
| `LIVE` | 定期（預設 10 min）拉取中央氣象署（CWA）開放資料：雷達回波、雨量、閃電、風場、能見度 |
| `SYNTHETIC` | 想定作者以 JSON 腳本注入虛構天氣（颱風、鋒面），支援時間軸關鍵影格插值 |
| `REPLAY` | 重播歷史天氣（AAR / golden replay 用） |

### 5.2 標準化輸出契約（`contracts/weather_payload.schema.json`）

模組每個天氣 tick（獨立於模擬 tick）發布 **格網化效果係數**，Core 不理解氣象學，只消費效果：

```json
{
  "issued_at_sim_tick": 4210,
  "cells": [{
    "h3_index": "884d290d8bfffff",
    "precipitation_mmhr": 12.5,
    "wind_ms": 8.2, "wind_dir_deg": 135,
    "visibility_m": 1200,
    "cloud_base_m": 800,
    "effects": {
      "rf_attenuation_db": 12.5,
      "mobility_modifier": 0.75,
      "sensor_optical_modifier": 0.4,
      "sensor_ir_modifier": 0.6,
      "uav_operability": false,
      "rotary_wing_operability": true,
      "artillery_dispersion_modifier": 1.15
    }
  }]
}
```

- 原始氣象值 → 效果係數的映射表定義於模組內 `effects_mapping.yaml`，White Cell 可於推演中調整（記入 Ledger）。
- CWA API 失效時模組 MUST 降級為「最後有效值 + `stale=true` 標記」，Core 收到 stale 超過 30 分鐘則向 White Cell 發告警。

---

## 6. 通訊與電磁模組（Comms/EW Module)

`modules/comms/`。整合 Terrain + Weather 輸出，模擬指管通情鏈路。

### 6.1 通訊模型

- 每個單位可掛載通訊裝備（LoRa / Meshtastic / VHF / SATCOM，定義於 EquipmentTemplate）。
- 每個通訊 tick 重算鏈路預算：`link_margin_db = tx_power + gains − path_loss(距離, 地形遮蔽) − weather_attenuation − jamming`。
- 鏈路狀態映射：`margin > 6dB → ONLINE`、`0~6dB → DEGRADED`、`< 0dB → OFFLINE`。
- Mesh 網路以圖論建模：單位間可 multi-hop 中繼；`networkx` 計算連通分量，孤島單位標記 `OFFLINE`。

### 6.2 通訊狀態的戰術後果（MUST enforce）

| 單位狀態 | 效果 |
|----------|------|
| `ONLINE` | 正常接收指令、即時回報位置 |
| `DEGRADED` | 指令延遲 N ticks 送達；位置回報降頻；AI 收到的敵情摘要粒度下降 |
| `OFFLINE` | 無法接收新指令；執行最後有效指令或 doctrine fallback；其位置對己方 COP 凍結為最後回報點（fog of war 對己方也成立） |

### 6.3 電子戰（Phase 1.5）

- 干擾器（Jammer）為一種 EquipmentTemplate：定義頻段、功率、方向性。
- 干擾效果由確定性公式計入 §6.1 的 `jamming` 項。
- 電偵（SIGINT）：偵測到發射源時產生 `EMISSION_DETECTED` 事件（帶測向誤差橢圓），供 AI 與玩家研判。

---

## 7. 戰鬥裁決引擎（Adjudication Engine）

位於 Core 內（`core/app/adjudication/`），**純函數、確定性、可單元測試**。這是 P1 原則的心臟：AI 永不裁決物理。

### 7.1 交戰裁決管線

```
EngagementOrder
  → [a] 合法性：射程 ∈ 武器包絡？LOS 或間瞄彈道可達？彈藥 > 0？
  → [b] 命中機率 P_hit = base_ph(weapon, range_band)
         × terrain_cover_modifier × weather_modifier
         × shooter_suppression_modifier × target_posture_modifier
  → [c] roll = DeterministicRNG(stream="adjudication").random()
  → [d] 命中 → 傷害 = damage_table(weapon, target_armor_class)，更新 healthStatus
  → [e] 產生 ENGAGEMENT_RESOLVED 事件（含所有中間係數，供 AAR 溯源）
```

- `base_ph`、`damage_table` 等武器參數表為 **資料驅動**（`EquipmentTemplate.baseStats` JSON + `contracts/weaponeering.schema.json` 驗證），不寫死在程式碼。
- 大部隊聚合戰鬥（營級以上）採用 **隨機化 Lanchester 方程**（aimed-fire square law / area-fire linear law 混合，係數由單位屬性推導），逐 tick 遞減雙方戰力，避免逐一單兵計算的效能爆炸。單位層級 ≤ 連級用個體裁決、≥ 營級用聚合裁決，閾值可於 Scenario 設定。
  **N 方（§12.1/ADR 006）**：聚合裁決以中性參數 `(force_a, force_b)` 定義；多方混戰＝對每一
  **HOSTILE 配對**逐一裁決（配對確定性排序）。事件欄位用 `initiator_loss`/`target_loss`
  （不用 blue/red 命名）。

### 7.2 偵測裁決（Sensor Model）

- 每個 sensor tick 對每對 (sensor, candidate_target) 計算偵測機率：距離衰減 × LOS × 天氣係數 × 目標特徵（尺寸/熱訊號/移動中）× 隱蔽姿態。
  候選配對依**關係矩陣**（§12.1）：`NEUTRAL` 與 `HOSTILE` 皆為可偵測對象（中立單位也會被觀測）；
  己方與 `ALLIED` 不列為 contact（盟軍互見經共享視圖，非偵測）。
- 偵測結果分級：`DETECTED（有東西）→ CLASSIFIED（類型）→ IDENTIFIED（敵我與型號）`，逐級需要更好的條件。
- 產生的情報進入 **per-faction intel store**：每一方看到的世界都是自己的偵測結果集合，而非 ground truth（fog of war 的實作基礎，見 §13.3）。
- **效能規約**：偵測掃描 MUST 使用空間索引（H3 k-ring 預過濾）將配對數從 O(N²) 降到近線性。

### 7.3 影像仲裁例外（Vision Arbiter）

- 依 SPEC.md §2.2：實兵結合虛擬（LVC）情境下的即時影像判定（如雷射接戰感應、空拍畫面計分）由 `modules/vision/` 的 **非 AI 確定性 CV 管線**（OpenCV 規則式）處理，完全繞過 LLM 節點，保證裁決嚴格依規則。
- 輸出與一般裁決事件同格式進入 Ledger，`adjudicated_by: "vision_arbiter_v{x}"`。

---

## 8. 後勤與持續戰力模型（Logistics Module）

Phase 1 內建於 Core（`core/app/engine/logistics.py`），Phase 2 可抽出為插件。

- **消耗模型**：每單位攜行量（彈藥 basic load、油料、水糧、電池）記於 `EquipmentInstance.currentState`；移動、交戰、待機各有消耗率表。
- **補給線**：補給單位沿路徑執行運補任務，路徑被截斷（敵佔領 hex / 橋樑摧毀）→ 補給失敗事件。
- **戰力影響（MUST enforce）**：彈藥耗盡的單位無法接戰；油料耗盡無法移動；補給中斷超過閾值 → `effectiveness_modifier` 遞減。
- **士氣與抑制（Suppression/Morale）**：受壓制單位命中率下降、移動受限；士氣由損失率、補給狀態、指揮鏈完整性推導（簡化 0–100 標量，公式資料驅動）。

---

## 9. AI 指揮參謀子系統（AI Subsystem）

`ai/` 目錄。Phase 1 單節點角色切換 → Phase 2 MoA 多節點。

### 9.0 AI 運作模式（AI Operation Modes）

**設計前提（2026-07 修訂）**：RAG 語料與 eval 案例可能長期不足（可得來源以公開文獻為主，
部分資料仰賴軍方提供、時程不定）。系統 MUST 在「語料/評測為空」時仍完整可用——AI 是**增強**，
不是**依賴**。三種模式（session 級設定 `ai_mode`，White Cell 可於局中調整）：

| 模式 | 行為 | 用途 |
|------|------|------|
| `AI_OFF` | AI 子系統完全停用：無 OPFOR 自主迴路、AI 端點回 `AI_DISABLED` 錯誤、前端隱藏 AI 面板。**紅軍由人操作**——系統即傳統兵推（物理引擎 + 人對人） | 傳統演習、無 AI 節點的部署、資安隔離場合 |
| `AI_BARE` | AI 啟用但**不接 RAG**：模型以自身軍事知識推理。輸出契約不變（schema/CoT/物理預檢/IHL 照常），唯引用規則反轉——`cited_documents` MUST 為空（詳見 §10 G5 模式感知） | RAG 語料未備妥時的預設 AI 模式 |
| `AI_FULL` | 完整管線：RAG 檢索 + 引用查核 + 全部護欄 | 語料入庫後 |

規則：
1. **預設模式**：`AI_OFF`（保守預設——沒有明示要 AI 就是傳統兵推）。
2. **自動降級**：`AI_FULL` 下若檢索時 RAG collection 為空/不可用 → 該次呼叫按 `AI_BARE` 語義
   處理並記 `RAG_UNAVAILABLE` 事件（Ledger）；不得因語料缺失而讓 AI 呼叫失敗。
3. **護欄不因模式縮水**：G1–G4、G6 在 `AI_BARE`/`AI_FULL` 一律生效；G5 依模式切換語義（§10）。
   `AI_OFF` 下無 AI 輸出，護欄自然不觸發——但 Gateway 本身不可移除（紅線 3）。
4. **eval gate 模式感知**：eval 案例庫為空時 gate 降為 schema-only + 顯式警告（§19.4）；
   `AI_BARE` 的 eval 不計引用正確率（無庫可引）。
5. 所有 AI 呼叫記錄（AIInvocationLog）含當時模式，AAR 可追溯「這局的 AI 是在什麼模式下運作」。

### 9.1 Phase 1：Role-Switching 單節點

- **硬體**：1× 中央伺服器 + 1× AI 運算節點（vLLM 服務 31B 等級模型）。
- **Role Manager**（`ai/inference/role_manager.py`）維護角色註冊表，依請求熱切換 System Prompt + LoRA adapter：

| 角色 | 職責 | 觸發方式 |
|------|------|----------|
| `STRATEGIC_PLANNER` | 藍軍參謀：接收指揮官意圖，產生行動方案（COA）建議 | 玩家請求 |
| `OPFOR_COMMANDER` | 紅軍指揮官：依紅軍準則對戰場變化做出決策 | Kernel 事件驅動（偵測到藍軍行動、每 N ticks 巡檢） |
| `AAR_ANALYST` | 賽後分析：從 Ledger 產生敘事與教訓 | Session 結束 / White Cell 請求 |
| `INTEL_OFFICER` | 情報整編：把零散 detection events 融合為敵情判斷（附信心度） | 每 N ticks / 玩家請求 |
| `WHITE_CELL_ASSISTANT` | 統裁輔助：建議注入事件、偵測推演失衡 | White Cell 請求 |

- **佇列策略**：角色切換有成本（LoRA swap ~秒級），Role Manager MUST 以角色分組批次處理請求，並保證 `OPFOR_COMMANDER` 佇列優先權最高（維持對抗即時性）。
- 所有 AI 請求／回應（含 prompt、CoT、latency、token 數）記入 `AIInvocationLog` 表。

### 9.2 AI 輸出契約

所有戰術輸出 MUST 是通過 JSON Schema 驗證的結構化指令，例如 OPFOR 決策：

```json
{
  "reasoning_chain": "1. 藍軍已於 H-45 高地建立觀測所…（MUST 非空，≥3 步驟）",
  "intent": "delay_and_attrit",
  "orders": [
    {"unit_id": "...", "order_type": "MOVE", "target_h3": "884d...", "posture": "BOUNDING_OVERWATCH"}
  ],
  "confidence": 0.72,
  "cited_documents": ["doctrine/red_delay_ops.md#L42"],
  "ihl_self_check": {"civilian_risk_assessed": true, "notes": "..."}
}
```

- Schema 驗證失敗 → 最多重試 2 次（附錯誤回饋）→ 仍失敗則 doctrine fallback + `AI_OUTPUT_REJECTED` 事件。
- AI 提出的每一個 order 仍要走 §2.3 的物理預檢——**AI 沒有繞過物理引擎的特權**。

### 9.3 Phase 2：Mixture-of-Agents（MoA）

依文獻回顧之 MoA 架構：

- **Proposers**：`INTEL / LOGISTICS / FIRES / MANEUVER` 四個特化小模型（各接專屬 RAG collection），平行產生候選方案。
- **Challenger**：對候選案進行對抗性審查（找戰術盲點、假設漏洞）。
- **Aggregator/Judge**：批判性綜合，產出最終建議 + 共識分數。
- **SPRT 動態終止**：每輪辯論後 Judge 輸出共識分數 ∈ [0,1]，以 Wald's SPRT 累積 log-likelihood ratio，跨越決策邊界（預設 A=ln(19), B=−ln(19)，即 95% 信賴）即終止辯論；並以 Kolmogorov–Smirnov 檢定偵測意見分佈穩定作為輔助停止條件。上限 5 輪防止算力失控。
- 辯論全文記入 Ledger 供 AAR 檢視「AI 為何這樣建議」。

### 9.4 RAG / 訓練管線（`ai/rag/`, `ai/training/`）

- **RAG**：Qdrant 向量庫，collection 按領域分割（`doctrine_blue`, `doctrine_red`, `doctrine_general`, `equipment_specs`, `terrain_analysis`, `historical_ops`）。入庫管線：markdown → 語意切塊（512 tokens, overlap 64）→ bge-m3 嵌入（中英雙語）→ 附 metadata（來源、密級、版本）。
  - **`doctrine_general`（2026-07 新增）**：無法歸屬紅/藍的通用軍事文獻——實務上可得語料多為
    公開出版品（美軍 FM 系列、智庫報告、DTIC 技術報告），其準則不專屬某一陣營。強行分類會製造
    假象；general collection 供所有角色檢索。紅/藍 collection 保留給真正陣營特化的語料（若有）。
  - **空語料是常態，不是錯誤**：任一 collection MAY 為空。檢索 API 對空庫回空結果 +
    `index_empty` 標記；上游依 §9.0 規則自動降級 `AI_BARE`，不失敗。
  - **PDF/掃描/圖檔不直接入庫**：先經文檔轉換管線（→ **SPEC_INGEST.md**，獨立子系統）產出
    結構化 markdown（含錨點與 front-matter）並經人工審核，才進 corpus。入庫 CLI 只吃 markdown。
- **RAFT**（依文獻 §四）：訓練樣本 = 問題 + 1 golden document + 3–5 distractor documents；模型 MUST 學會忽略干擾文件並「逐字引用」golden 內容 + CoT。合成資料由教師模型從準則文件自動生成（`ai/training/raft_datagen.py`）。
- **CPT:SFT 預算**：遵循 D-CPT Law，約 99.99% token 預算投入 CPT（軍事準則語料）、0.01% 投入 SFT（格式對齊），比例為設定檔參數非硬編碼。
- **評測**：建立 WARBENCH 風格內部基準（`ai/evals/`）：IHL 兩難情境、殘缺情報（抽離 20–80% 要素）、注入矛盾假情報三類壓力測試。模型/adapter 更新 MUST 通過 eval gate 才可部署（門檻見 §19.4）。

---

## 10. 反幻覺與合規護欄（Guardrails）

`core/app/guardrails/`，位於 AI Node 與 Ledger 之間的強制閘道（Guardrail Gateway）。任何 AI 輸出 MUST 依序通過：

| # | 檢查 | 失敗處置 |
|---|------|----------|
| G1 | **JSON Schema 驗證** | 重試 ≤2 → fallback |
| G2 | **CoT 存在性與最小長度**（≥3 推理步驟，WARBENCH 顯示 CoT 為結構性防護網） | 退回重生成 |
| G3 | **物理可行性**：所有 order 交 Terrain/Comms 預檢 | 逐條剔除不可行 order 並記錄 |
| G4 | **IHL/ROE 合規檢查**：規則引擎比對目標清單與保護目標資料庫（醫院、文化資產、平民區 hex 標記）；比對想定 ROE（如「不得越過某線」「禁用某類武器」）；**N 方（§12.1）**：對 `ALLIED`/`NEUTRAL` 陣營單位的打擊一律攔截（friendly fire / 攻擊中立＝ROE 違規） | **硬性阻擋**，事件升級 White Cell 人工裁定 |
| G5 | **引用查核（模式感知，§9.0）**：`AI_FULL` → `cited_documents` 必須存在於 RAG 庫且相似度 > 閾值（防捏造引用）；`AI_BARE` 或庫空 → `cited_documents` MUST 為**空**，任何非空引用一律視為捏造 | `AI_FULL`：標記 `citation_unverified`，降信心度；`AI_BARE`：剔除引用欄 + 記捏造事件 |
| G6 | **量化模型加嚴**：若當前 adapter 為 ≤8-bit 量化部署（WARBENCH 顯示違規率飆升），G4 改為「白軍逐條確認模式」 | — |

- 護欄的每次攔截都是 Ledger 事件（`GUARDRAIL_INTERVENTION`），AAR 可統計 AI 可靠度。
- 保護目標資料庫（No-Strike List）為 Scenario 資產，想定編輯器可繪製保護區 polygon → 轉為 hex 標記。

---

## 11. 想定管理系統（Scenario Management）

### 11.1 Scenario Package 格式

一個想定是一個版本化的 zip/目錄（`scenarios/<name>/`）：

```
scenario.yaml          # 元資料：名稱、bbox、模式、tick 設定、勝利條件、
                       # factions 清單（id/顯示名/顏色）+ relations 關係矩陣（§12.1，ADR 006）
orbat/<faction>.yaml   # 各陣營戰鬥序列（每個 scenario.yaml 宣告的交戰陣營一檔，N 方任意數量）
roe.yaml               # 交戰規則 + No-Strike List(GeoJSON)
msel.yaml              # 事件注入清單：{trigger: {type: time|condition, ...}, inject: {...}}
weather_script.yaml    # (可選) SYNTHETIC 天氣腳本
overrides/             # (可選) mobility matrix、weaponeering 覆寫
```

- **factions/relations 為 scenario 權威**（§12.1）：`factions:` 定義本局合法陣營 id
  （`WHITE_CELL` 保留字不可用）；`relations:` 上三角宣告配對關係，未宣告預設 `NEUTRAL`；
  victory_conditions 的 `faction` 可為任一已宣告陣營。

- Schema 定義於 `contracts/scenario.schema.json`；載入時 MUST 全量驗證，錯誤需給出精確路徑（如 `orbat/blue.yaml: units[3].equipment[0]: unknown template 'T-999'`）。

### 11.2 想定編輯器（前端）

- 地圖上拖放單位、繪製控制措施（相位線、目標區、保護區）。
- ORBAT 樹狀編輯器（THEATER→…→FIRETEAM 十級，對應 `UnitLevel`）。
- MSEL 時間軸編輯器：時間觸發與條件觸發（如「藍軍任一單位進入 hex 區域 X」）。
- 匯出/匯入 Scenario Package；內建 3 個官方範例想定（教學用小型、營級防禦、聯合防衛大型）。

### 11.3 MSEL 觸發引擎

- Kernel 的 `check_triggers()` 每 tick 評估條件觸發器；觸發後注入事件（增援出現、橋樑損毀、假情報投放、天氣突變…）。
- White Cell 可即時手動注入任何 MSEL 事件或臨時事件（ad-hoc inject）。

---

## 12. 使用者角色與權限（RBAC）

| 角色 | 權限 |
|------|------|
| `EXERCISE_DIRECTOR`（白軍主席） | 全知視圖、時間控制、回滾、注入、否決 AI、修改 ROE |
| `WHITE_CELL_STAFF` | 全知視圖、注入（需主席核可的除外） |
| `BLUE_COMMANDER` / `RED_COMMANDER` | 本軍視圖（fog of war 過濾）、對本軍單位下令、請求 AI 參謀 |
| `BLUE_STAFF` / `RED_STAFF` | 本軍視圖、受指揮官授權的子集單位下令 |
| `OBSERVER` | 指定視圖唯讀（可設定為全知或單方） |
| `ANALYST` | 僅 AAR 與歷史 Session 存取 |

- 認證：自建帳號 + Argon2id 雜湊 + TOTP 2FA（系統需可離線部署，不依賴外部 IdP）；Session token 用 JWT（短效）+ refresh。
- **每一條 API 都要通過 faction-scope 檢查**：例如 RED 玩家呼叫 `GET /units` 只會得到紅軍單位 + 紅軍情報所見的其他陣營 contact。此過濾 MUST 在後端做，前端過濾不可信。
- 全部管理操作進 audit log（獨立於戰術 Ledger）。
- 角色表中的 `BLUE_COMMANDER`/`RED_COMMANDER` 為兩軍時代的示例命名——實際為
  `COMMANDER`/`STAFF` 角色 × `SessionParticipant.faction` 綁定，N 方下自然擴展（§12.1）。

### 12.1 多陣營模型與關係矩陣（2026-07 修訂，ADR 006）

系統 MUST 支援 **N 個交戰陣營**（如藍、紅、黃三軍）與可設定的**陣營關係矩陣**。

**Faction 模型**：
- `faction` 為**想定定義的字串 id**（pattern `^[A-Z][A-Z0-9_]{1,31}$`），非封閉 enum。
  合法值集合由 scenario 的 `factions:` 清單定義（含顯示名與顏色），載入時全量驗證。
- **`WHITE_CELL` 為保留字**：統裁視角、非交戰方，不得出現於 orbat 與關係矩陣。

**關係矩陣（FactionRelation）**：
- 三值 `ALLIED` / `NEUTRAL` / `HOSTILE`，**對稱**（A↔B 同值）。
- 想定宣告上三角配對（`relations: [[A, B, HOSTILE], …]`）；**未宣告配對預設 `NEUTRAL`**
  ——想定必須明示敵對，防止「忘了設定＝全面開戰」。
- White Cell 可於局中調整（宣戰/停火）→ `FACTION_RELATION_CHANGED` Ledger 事件（證據性、可重播）。
- **單一權威**：`core/app/factions/` 關係服務。任何子系統 MUST 經其查詢敵我
  （`is_hostile/is_allied/is_neutral`），**禁止自行以 `faction != mine` 判敵**（紅線）。

**各子系統語義**：

| 查詢 | ALLIED | NEUTRAL | HOSTILE |
|------|--------|---------|---------|
| 偵測（成為 contact） | 否 | 是 | 是 |
| ENGAGE 物理預檢 | 拒（friendly fire 僅 White Cell override） | 拒（ROE 違規） | 允許 |
| 護欄 G4 | 攔截 | 攔截 | 通過 |
| 聚合裁決配對 | 不配對 | 不配對 | 配對（每一 HOSTILE 配對逐一裁決） |
| 情報共享 | 可（等級可設，Phase 2 細化） | 否 | 否 |

前端 affiliation（§13.2）：own=F、ALLIED=F、NEUTRAL=N、HOSTILE=H；faction 顯示色由
scenario `factions[].color` 提供。任務卡：TASKS O6.7–O6.10；聚合裁決泛化見 §7.1。

---

## 13. 前端與共同作戰圖像（COP / Frontend）

`platform/`（Nuxt 4）。

### 13.1 頁面結構

```
/login
/lobby                     # Session 列表、建立、加入
/scenario-editor           # §11.2
/session/:id/cop           # 主作戰圖像（依角色渲染不同工具列）
/session/:id/orders        # 指令管理（下達、待決、歷史）
/session/:id/white-cell    # 白軍控制台（時間、注入、AI 監控、護欄事件）
/session/:id/aar           # AAR 儀表板（Session 結束後）
/admin                     # 使用者/系統管理
```

### 13.2 COP 地圖需求

- MapLibre GL + 自建離線 tile server（OpenMapTiles）+ DTED hillshade 疊加層。
- 單位以 MIL-STD-2525D 符號渲染（milsymbol），縮放時按 UnitLevel 聚合（拉遠只顯示營級以上）。
  **N 方 affiliation（§12.1）**：符號敵我識別由關係矩陣推導——own/ALLIED=F、NEUTRAL=N、
  HOSTILE=H；同 affiliation 的不同陣營以 scenario `factions[].color` 區分（三方混戰時
  兩個敵對陣營需可視覺區分）。
- 圖層開關：六角網格、天氣、通訊連線圖、偵測範圍、控制措施、補給線。
- **效能**：500 單位 + 天氣層同時渲染 MUST 維持 ≥30 FPS（deck.gl instanced rendering）。
- 即時性：WebSocket 增量更新（diff-based），斷線自動重連 + 以 `last_event_id` 補償（見 §16.3）。

### 13.3 Fog of War 渲染

- 己方單位：實線符號 + 完整資訊；但 `OFFLINE` 單位顯示為「最後回報位置 + 經過時間」虛影。
- 敵方 contact：依情報等級渲染——`DETECTED`（未知菱形）→ `CLASSIFIED`（類型符號）→ `IDENTIFIED`（完整符號），並顯示情報時效（愈舊愈透明）。
- White Cell 可切換 ground truth / 任一方視角（用於統裁與教學）。

### 13.4 指令下達 UX

- 地圖直接互動：選單位 → 右鍵/長按 → 指令面板（移動、攻擊、偵察、防禦姿態、補給）。
- 每筆指令顯示裁決前物理預檢結果（可達性、預估 ETA、彈藥狀況）。
- AI 參謀面板：向 `STRATEGIC_PLANNER` 請求 COA 建議 → 以「草稿指令組」呈現 → 指揮官可逐條修改/採納/拒絕（P2 人在迴路）。CoT 摘要一鍵展開。

---

## 14. 行動後檢討系統（AAR System）

### 14.1 資料基礎

Event Ledger 是不可變、完整、含快照（天氣/地形係數/CoT）的事實來源——AAR 全部由它推導，不需要另外收集。

### 14.2 功能

| 功能 | 說明 |
|------|------|
| **時間軸重播** | 任意速度播放/倒帶整場推演（前端從 Ledger 流式重建狀態）；可跳至書籤事件 |
| **統計儀表板** | 雙方戰損交換比、彈藥消耗、偵測成功率、指令延遲分佈、通訊中斷時長、護欄攔截統計 |
| **熱區圖** | 交戰密度、傷亡密度、偵測覆蓋的 hex heatmap |
| **決策樹檢視** | 每個關鍵事件可下鑽：當時的天氣快照、地形係數、AI CoT 全文、命中計算的每一項係數 |
| **AI 敘事報告** | `AAR_ANALYST` 角色從 Ledger 生成結構化報告：戰役經過摘要、關鍵轉折點（由統計偵測 + AI 詮釋）、雙方教訓（sustain/improve）、IHL 事件回顧。報告 MUST 逐段引用 event id 供查證 |
| **假設分析（Phase 2）** | 從任一檢查點 fork 出分支 Session，改變單一變數重跑（"what-if"） |
| **匯出** | PDF 報告 + 完整 Ledger JSON/CSV（學術研究用，含匿名化選項） |

---

## 15. 資料庫架構（Database Schema）

### 15.1 沿用 SPEC.md 的表

`SystemConfiguration, WargameSession, TacticalUnit, EquipmentTemplate, EquipmentInstance, TacticalEventLog` 全部保留，欄位不變（權威定義移至 `db/prisma/schema.prisma`）。

### 15.2 新增表（完整 schema 見 `db/prisma/schema.prisma`）

```prisma
model User {
  id            String   @id @default(uuid())
  username      String   @unique
  passwordHash  String
  totpSecret    String?
  role          UserRole
  createdAt     DateTime @default(now())
  participants  SessionParticipant[]
}
enum UserRole { EXERCISE_DIRECTOR, WHITE_CELL_STAFF, COMMANDER, STAFF, OBSERVER, ANALYST, ADMIN }

model SessionParticipant {          // 使用者 × Session × 陣營的綁定
  id        String @id @default(uuid())
  userId    String
  sessionId String
  faction   String   // 想定定義的陣營 id（§12.1/ADR 006；O6.7 前暫為 enum Faction）
  role      UserRole
  unitScope Json     // 可指揮的單位 id 白名單（null = 全軍）
  @@unique([userId, sessionId])
}
// N 方修訂（§12.1，O6.7 落地）：所有 faction 欄位（TacticalUnit/SessionParticipant/IntelContact）
// 由 enum Faction 遷移為 String（WHITE_CELL 保留字）；關係矩陣為 session 熱狀態 +
// FACTION_RELATION_CHANGED Ledger 事件（可重播），不另設 DB 表。

model Scenario {
  id          String   @id @default(uuid())
  name        String
  version     String
  packageBlob Bytes    // 或改存物件儲存路徑
  checksum    String
  createdBy   String
  createdAt   DateTime @default(now())
}

model Order {                        // 指令生命週期（Ledger 記事件，此表記狀態機）
  id           String   @id @default(uuid())
  sessionId    String
  issuerId     String   // SessionParticipant 或 AI role
  unitId       String
  orderType    String   // MOVE, ENGAGE, RECON, RESUPPLY, POSTURE, ...
  payload      Json
  status       OrderStatus @default(PENDING)   // PENDING→VALIDATED→EXECUTING→COMPLETED/REJECTED/CANCELLED
  precheck     Json?    // 物理預檢結果快照
  issuedAtTick Int
  resolvedAtTick Int?
  @@index([sessionId, status])
}
enum OrderStatus { PENDING, VALIDATED, EXECUTING, COMPLETED, REJECTED, CANCELLED }

model IntelContact {                 // per-faction 情報視圖（fog of war 基礎）
  id            String @id @default(uuid())
  sessionId     String
  faction       Faction              // 這是「誰看到的」
  targetUnitId  String               // ground truth 連結（前端永不直接收到）
  fidelity      IntelFidelity        // DETECTED / CLASSIFIED / IDENTIFIED
  lastSeenTick  Int
  lastSeenLat   Float
  lastSeenLng   Float
  errorRadiusM  Float
  @@index([sessionId, faction])
}
enum IntelFidelity { DETECTED, CLASSIFIED, IDENTIFIED }

model SimCheckpoint {
  id          String   @id @default(uuid())
  sessionId   String
  tick        Int
  stateBlob   Bytes    // zstd 壓縮的完整狀態
  stateHash   String   // replay 驗證用
  createdAt   DateTime @default(now())
  @@unique([sessionId, tick])
}

model AIInvocationLog {
  id           String   @id @default(uuid())
  sessionId    String?
  role         String   // STRATEGIC_PLANNER / OPFOR_COMMANDER / ...
  adapter      String   // LoRA adapter 版本
  promptHash   String
  request      Json
  response     Json
  latencyMs    Int
  tokensIn     Int
  tokensOut    Int
  guardrailResult Json  // 各護欄通過/攔截明細
  createdAt    DateTime @default(now())
  @@index([sessionId, role])
}

model AARReport {
  id          String   @id @default(uuid())
  sessionId   String   @unique
  narrative   Json     // AI 敘事報告（結構化段落 + event id 引用）
  metrics     Json     // 預計算統計
  generatedAt DateTime @default(now())
}

model PluginRegistry {
  id          String   @id @default(uuid())
  name        String   @unique
  kind        String   // TERRAIN / WEATHER / VISION / AI_ROLE / CUSTOM
  endpoint    String
  contractVer String
  healthState String   // HEALTHY / DEGRADED / DOWN
  config      Json
  enabled     Boolean  @default(true)
}
```

### 15.3 Event Ledger 補充規範

- `TacticalEventLog` 為 **append-only**：應用層禁止 UPDATE/DELETE；MariaDB 帳號權限層面對該表 revoke UPDATE/DELETE 以硬性保證。
- 每事件加入 `seq`（session 內單調遞增，由 Kernel 發號）與 `prevHash`/`selfHash` 鏈式雜湊欄位，形成 tamper-evident hash chain，支撐 AAR 的證據性。
- `detail` 欄承載**非證據性診斷**（如 TICK_OVERRUN 的牆鐘耗時、ROLLBACK 中繼資料），**刻意不納入 hash**——非決定性值入鏈會破壞重播可重現性；證據性欄位一律不得放 detail。
- **rollback 後的時間軸身分**：ledger 完整保留被棄世代（append-only），故 rollback 後 `tick` 非單調；以 `seq` 作時間軸身分（checkpoint 錨定 `ledgerSeq`、事件計數以 seq 為準）。
- `eventType` 枚舉（初版）：`ORDER_ISSUED, ORDER_REJECTED, MOVEMENT_STEP, ENGAGEMENT_RESOLVED, DETECTION, COMMS_STATE_CHANGED, RESUPPLY, MSEL_INJECT, TERRAIN_MODIFIED, WEATHER_UPDATE, GUARDRAIL_INTERVENTION, AI_OUTPUT_REJECTED, TIME_COMPRESSION_CHANGED, CHECKPOINT, ROLLBACK, TICK_OVERRUN, SESSION_STATE_CHANGED`。

### 15.4 Prisma 與 Python 的共存策略

- **Prisma schema 是唯一權威**；Nuxt 端（BFF 輕查詢）直接用 Prisma Client。
- Python Core 使用 SQLAlchemy 2.0，models 由 CI 腳本 `tools/schema_sync_check.py` 與 `schema.prisma` 做結構比對，drift 即 CI 失敗。Migration 一律由 `prisma migrate` 產生與執行，Python 端永不自行 migrate。

---

## 16. API 與通訊協定契約

契約檔案一律先行放在 `contracts/`（OpenAPI 3.1 / proto3 / JSON Schema），程式碼由契約生成或驗證，**手寫端點與契約不一致視為 bug**。

### 16.1 REST（Core，摘要；完整見 `contracts/core_api.yaml`)

```
POST   /api/v1/auth/login | /refresh | /logout
GET    /api/v1/sessions                     # 依角色過濾
POST   /api/v1/sessions                     # body: {scenario_id, config}
POST   /api/v1/sessions/{id}/lifecycle      # {action: START|PAUSE|RESUME|END|ROLLBACK, checkpoint_tick?}
GET    /api/v1/sessions/{id}/state          # faction-scoped 全量快照（重連用）
GET    /api/v1/sessions/{id}/units          # faction-scoped
POST   /api/v1/sessions/{id}/orders         # 下令（回傳 precheck 結果）
DELETE /api/v1/sessions/{id}/orders/{oid}   # 取消未執行指令
POST   /api/v1/sessions/{id}/ai/consult     # {role, query} → 202 + task_id（非同步）
GET    /api/v1/sessions/{id}/ai/tasks/{tid}
POST   /api/v1/sessions/{id}/injects        # White Cell only
GET    /api/v1/sessions/{id}/ledger?after_seq=&types=   # 分頁事件查詢（faction-scoped）
GET    /api/v1/sessions/{id}/aar
POST   /api/v1/scenarios (multipart upload) / GET /api/v1/scenarios
GET    /api/v1/admin/plugins | POST /api/v1/admin/plugins/{name}/toggle
```

- 錯誤格式統一：`{"error": {"code": "ORDER_OUT_OF_RANGE", "message": "...", "details": {...}}}`，error code 枚舉列於契約。

### 16.2 WebSocket 協定（`contracts/ws_protocol.md`）

- 連線：`WS /api/v1/sessions/{id}/stream?token=...`，伺服器依 token 的 faction scope 過濾一切訊息。
- Envelope：

```json
{"v": 1, "seq": 10231, "tick": 4211, "type": "STATE_DIFF", "payload": {...}}
```

- 訊息類型：`HELLO`（含 last_seq）、`STATE_DIFF`（單位增量）、`EVENT`（Ledger 事件的 faction-safe 投影）、`INTEL_UPDATE`、`WEATHER_UPDATE`、`CLOCK`（tick/壓縮比心跳）、`AI_TASK_UPDATE`、`ERROR`。
- **重連補償**：client 帶 `last_seq` 重連，server 從 Redis ring buffer 補送缺漏（保留最近 5000 條）；超出範圍則指示 client 走 `GET /state` 全量重同步。

### 16.3 Plugin 契約（gRPC，`contracts/plugin_base.proto`）

所有插件 MUST 實作基礎服務：

```proto
service PluginBase {
  rpc GetManifest(Empty) returns (Manifest);      // name, kind, contract_version, capabilities
  rpc HealthCheck(Empty) returns (Health);        // HEALTHY/DEGRADED/DOWN + detail
  rpc Configure(PluginConfig) returns (Ack);      // 熱更新設定
}
```

- 版本相容規則：Orchestrator 啟動時比對 `contract_version`（semver）；major 不合 → 拒絕載入；minor 落後 → 警告降級。
- 心跳：Orchestrator 每 10s 健檢；連續 3 次失敗 → 標記 DOWN → 依插件 kind 執行預案（Weather DOWN → stale 模式；Terrain DOWN → Session 強制 PAUSE，因為物理預檢是硬依賴）。

---

## 17. 插件系統規格（Plugin System）

- **註冊**：插件啟動後向 `POST /api/v1/admin/plugins/register` 自報 manifest，或由設定檔靜態宣告。
- **隔離**：每個插件獨立 process/container，崩潰不得影響 Core（gRPC deadline + circuit breaker）。
- **開發套件**：`modules/_sdk/` 提供 Python base class（`MatsoPlugin`），內建 manifest/health/config 樣板與整合測試 harness——寫一個新插件只需實作領域邏輯（見 HOW_TO.md §插件開發指南）。
- **官方插件清單（Phase 1）**：`terrain`（硬依賴）、`weather`、`vision`；（Phase 1.5+）：`comms-ew`、`landuse`、`atak-bridge`（ATAK/TAK server 介接，SPEC.md `integrationConfig` 既定方向）。

---

## 18. 非功能需求（NFR）

| 類別 | 需求 |
|------|------|
| **規模** | 單 Session：≤500 作戰單位、≤40 併發使用者；單部署 ≤3 併發 Session |
| **延遲** | 指令物理預檢 p99 < 50ms；tick 計算 p99 < 200ms（500 單位）；WS 推播端到端 p95 < 500ms；AI 諮詢為非同步（目標 < 30s，逾時 fallback） |
| **可用性** | 推演進行中 Core 可用性 99.5%；崩潰後 5 分鐘內可由 checkpoint+ledger 復原（RTO ≤ 5min）。**事件** RPO = 0（ledger 落庫即不遺失）；**狀態** Phase 1 復原至最近 checkpoint（間隔預設 300 ticks），checkpoint 之後的前滾（由 ledger 指令重播補齊）於 O3.1 orders 落地後接上（見 §3.2、PROGRESS R5） |
| **安全** | 全站 TLS；faction-scope 後端強制；audit log 不可變；密碼 Argon2id；rate limiting；想定資料視為敏感——**支援完全離線（air-gapped）部署**，一切依賴（地圖 tile、模型權重、套件）可本地化 |
| **資料保存** | Ledger 與 AAR 永久保存；AIInvocationLog 保存 ≥1 年；個資去識別化匯出選項 |
| **國際化** | UI 正體中文優先，i18n 架構預留英文 |
| **授權合規** | 僅使用可離線商用的開源元件（MIT/Apache/BSD/MPL）；GPL 元件需隔離為獨立 process |

---

## 19. 測試與驗證策略

（執行細節見 HOW_TO.md；此處定義驗收門檻。）

### 19.1 決定性測試（最高優先）

- `tests/replay/`：每個 release 前跑 3 個 golden scenario 全程重播，最終 `stateHash` 必須與 golden 記錄完全一致。任何 PR 改變 hash MUST 附帶裁決邏輯變更說明並更新 golden。

### 19.2 物理引擎驗證

- 裁決引擎每條公式有 property-based tests（Hypothesis）：如「距離增加 → P_hit 單調不增」「遮蔽 hex 中 LOS 必為 false」。
- LOS 對照組：與 GRASS GIS `r.viewshed` 在 100 個抽樣點的結果比對，一致率 ≥98%。

### 19.3 一般工程門檻

- Core 與 modules：pytest 覆蓋率 ≥80%（adjudication/guardrails ≥95%）；mypy --strict 零錯誤。
- 前端：Vitest 單元 + Playwright E2E（登入→建局→下令→看到裁決結果 的煙霧測試）。
- 契約測試：schemathesis 對 OpenAPI 自動 fuzz；proto 向後相容檢查（buf breaking）。

### 19.4 AI 評測門檻（eval gate）

| 指標 | 門檻 |
|------|------|
| 結構化輸出 schema 通過率 | ≥98% |
| IHL 情境違規率（內部 WARBENCH 集） | ≤2%（含護欄後 = 0，護欄前模型原始 ≤10%） |
| 殘缺情報情境下的引用正確率 | ≥90% |
| 捏造引用率 | ≤1% |

**條件式 gate（2026-07 修訂，配合 §9.0）**：eval 案例可能長期不足（同語料）。
- 案例庫**空** → gate 降為 **schema-only**（僅驗輸出契約）+ CI 顯式警告 `EVAL_CORPUS_EMPTY`
  ——綠燈不代表戰術品質有保證，只代表管線正確。
- `AI_BARE` 模式的 eval 不計「引用正確率」（無庫可引）；「捏造引用率」語義反轉為
  「`cited_documents` 非空即捏造」。
- **真模型上生產（任何 `AI_BARE`/`AI_FULL` 正式演習）前 MUST 有最小案例集**（每角色×每壓力
  類型 ≥1，共 ≥15 例）跑過完整四門檻——這是部署 checklist 項目，不是 CI 綠燈可替代。

---

## 20. 部署架構

### 20.1 Phase 1 拓撲（Docker Compose，`ops/compose/`）

```yaml
services:
  core:        # FastAPI orchestrator
  frontend:    # Nuxt (SSR)
  mariadb:
  redis:
  qdrant:
  terrain:     # modules/terrain
  weather:     # modules/weather
  tileserver:  # 離線地圖 tile
  prometheus:
  grafana:
# AI 節點獨立主機：vLLM systemd 服務，core 以 OPENAI_BASE_URL 指向
```

### 20.2 硬體基線

- 中央伺服器：16 core / 64GB RAM / NVMe 1TB（DTED memmap + DB）。
- AI 節點：2× 24GB GPU（31B 4-bit 推論 + LoRA swap）或 1× 80GB（16-bit，建議——見 WARBENCH 量化風險）。

### 20.3 觀測性

- 每 tick 匯出 metrics：`tick_duration_ms, orders_processed, ai_queue_depth, guardrail_blocks_total, ws_clients, plugin_health`。
- Grafana 內建「推演健康」儀表板；`TICK_OVERRUN`、plugin DOWN、AI 逾時率 >20% 三者觸發告警。

---

## 21. 開發路線圖（Roadmap）

| 里程碑 | 內容 | 驗收標準（DoD） |
|--------|------|-----------------|
| **M0 基礎設施** | Monorepo 工具鏈、契約骨架、Docker Compose、CI | `docker compose up` 全服務健檢通過；CI 綠 |
| **M1 模擬骨幹** | SimClock、Kernel tick loop、Redis 狀態、Ledger（含 hash chain）、checkpoint/replay | golden replay 測試通過；崩潰復原演練通過 |
| **M2 地理引擎** | Terrain module 全 API、hex grid、LOS、A* 路徑 | §4.3 SLA 達標；LOS 對照 ≥98% |
| **M3 裁決核心** | Order pipeline、物理預檢、交戰/偵測裁決、fog of war intel store | 兩人可透過 API 對戰一場腳本化想定 |
| **M4 前端 COP** | 地圖、單位渲染、下令 UX、WS 即時更新、fog of war 渲染 | Playwright 煙霧測試通過；30 FPS 達標 |
| **M5 環境模組** | Weather module（LIVE+SYNTHETIC）、Comms 模組、效果整合進裁決 | 天氣改變可觀測地影響命中/移動/通訊 |
| **M6 AI Phase 1** | vLLM 接入、Role Manager、5 角色 prompt、Guardrail Gateway、RAG 管線 | eval gate 全過；OPFOR 可自主應對玩家 |
| **M7 想定與白軍** | Scenario package + 編輯器、MSEL 觸發、白軍控制台、RBAC 完整 | 3 個官方想定可完整演練 |
| **M8 AAR** | 重播、儀表板、AI 敘事報告、匯出 | 一場完整推演 → 產出可交付 AAR 報告 |
| **M9（Phase 2）** | MoA 多節點、SPRT 辯論、what-if 分支、ATAK bridge、RAFT 訓練管線 | 依 Phase 2 附錄另訂 |

依賴關係：M1→M2→M3→{M4, M5}→M6→M7→M8。M4 與 M5 可平行。

---

*本規格為活文件。修訂需經架構負責人核可，並同步更新 HOW_TO.md 對應章節與 `contracts/` 檔案。*
