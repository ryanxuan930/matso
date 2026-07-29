# 全域參數清冊（#93 第一步）

> 目的：把散落在 compose env、`Settings`、與程式常數裡的**全域可調參數**攤開，逐項標記
> 「改了要多久才生效」。**先有這份清冊，才決定哪些搬進系統設定頁**——因為多數常數目前是
> import 時綁定的，要能熱改得先改成從設定讀，那是實作成本而非 UI 問題。
>
> 掃描範圍：`core/app`、`modules/{terrain,weather,comms,_sdk}`、`ops/compose`、`platform`。
> 不含：每局參數（想定/ORBAT/關係矩陣/AI 指派——那些屬該局資料，不是全域設定）。

## 生效層級（本檔的分類軸）

| 層級 | 意思 | 目前有幾項 |
|---|---|---|
| **H 熱更新** | 存 DB 單例 `SystemConfiguration`，讀取端每次用時才取 → 改完即生效 | 4 |
| **R 重啟該局** | 於 session runner 建構時綁定 → 該局重跑（或封存後複製）即可，**不必重啟容器** | 7 |
| **C 冷啟動** | 由容器 env 注入 `Settings` → 需重啟該服務容器 | 12 |
| **P 需改程式** | 目前是 import 時綁定的模組常數 → 要熱改**得先改成從設定讀**（尚未做） | 25+ |

---

## H — 熱更新（已具備機制）

存於 `SystemConfiguration.integrationConfig`（DB 單例，免 migration），
`GET/PUT /api/v1/system/config` 讀寫，系統設定頁已可編輯。

| 參數 | 位置 | 說明 |
|---|---|---|
| `ai.mode` | `api/system.py` | AI 運作模式（AI_OFF / AI_BARE / AI_FULL）。未設時回退 env `MATSO_AI_MODE` |
| `ai.llm_base_url` | 同上 | LLM 端點（Ollama / vLLM / 雲端） |
| `ai.llm_model` | 同上 | 模型 id |
| `ai.llm_api_key` | 同上 | 金鑰（回應遮罩，只回 `llm_api_key_set`） |

---

## R — 重啟該局 runner（不必重啟容器）

於 `sim_runtime._run_session` 建構 Kernel 時綁定。改設定後，該局需停止再起
（封存/還原、或複製為新局）才會套用。

| 參數 | 位置 | 現值 | 影響 |
|---|---|---|---|
| `_TICK_RATE_MS` | `sim_runtime.py:57`（＝`movement/params.MOVE_TICK_RATE_MS`） | 60000 | 1 tick ＝ 幾分 sim 時間 |
| `_PACE_COMPRESSION` | `sim_runtime.py:58` | 120.0 | 真實節奏（0.5s/tick） |
| `SensorSweepSystem.interval_ticks` | `intel/sensor_system.py:64` | 5 | 偵測掃描頻率（#97） |
| `CommsSystem.interval_ticks` | `engine/comms.py:52` | 5 | 通聯重算頻率 |
| AI `heartbeat_s` | `ai_loop/orchestrator.py:162` | 45.0 | AI 決策心跳（**已可 per-session 由 autonomy 設定覆寫**） |
| `_MAX_TOTAL_ORDERS` | `ai_loop/worker.py:44` | 500 | AI runaway 守衛（單 worker 累計落單上限） |
| `victory` 輪詢 `_DEFAULT_POLL_S` | `ai_loop/victory.py:28` | 5.0 | 勝負監視器輪詢間隔 |
| `checkpoint_interval_ticks` | `sim_params.py`（WP-E1） | 600 | 狀態快照間隔（≈5 分鐘牆鐘 @ 0.5s/tick）；崩潰最多回退一個間隔 |

---

## C — 冷啟動（改 env → 重啟該容器）

`core/app/config.py::Settings`，由 `ops/compose/docker-compose.yml` + `ops/compose/.env` 注入。

| env | 現值/預設 | 服務 | 備註 |
|---|---|---|---|
| `MATSO_ENV` | development | core | production 時對不安全設定 fail-fast |
| `DATABASE_URL` | mariadb:3306（對外 3307） | core | |
| `REDIS_URL` | redis://localhost:6379/0 | core | |
| `TERRAIN_GRPC_TARGET` | terrain:50051 | core | |
| `WEATHER_GRPC_TARGET` | weather:50052 | core | |
| `JWT_SECRET` | 開發預設 | core | **正式部署 MUST 覆寫**（目前仍是預設，啟動有警告） |
| `ACCESS_TOKEN_TTL_S` / `REFRESH_TOKEN_TTL_S` | 900 / 1209600 | core | |
| `CORS_ORIGINS` | http://localhost:3000 | core | |
| `STUB_GATEWAY` | false | core | E2E 用；正式部署絕不設 |
| `MATSO_AI_MODE` | AI_OFF | core | 僅在 DB 未設 `ai.mode` 時作為回退 |
| `OPENAI_BASE_URL` / `MATSO_LLM_MODEL` | Ollama | core | 同上，DB 設定優先 |
| `M200_MAPS` / `MBTILES_DIR` | /Volumes/M200/Maps | terrain / tileserver | 掛載路徑 |

**前端另計**（build/啟動時綁定，見 `platform/.env.example`）：
`NUXT_PUBLIC_API_BASE`、`NUXT_PUBLIC_TILE_URL`、`NUXT_PUBLIC_ONLINE_BASEMAPS`、
`NUXT_PUBLIC_SATELLITE_URL`、`NUXT_PUBLIC_BASEMAPS`。
容器需 `docker compose build frontend && up -d frontend`；本機 dev 改 `platform/.env` 後重啟。

---

## P — 需改程式才能熱更新（目前 import 時綁定）

這些是**兵推行為**的參數，也是最值得做成可調的一群；但目前都是模組常數，
要能在 UI 改，得先把讀取端改成「從設定讀，缺值退預設」。

### 移動（`movement/params.py`）
`MOVE_SPEED_KMH` 40、`FOOT_XC_KMH` 5.0、`FOOT_ROAD_KMH` 6.5、
`MARCH_ATTRITION_PER_KM`（FOOT 0.05 / WHEELED 0.02 / TRACKED 0.03）、
`TEMPO_SPEED_FACTOR` / `TEMPO_ATTRITION_FACTOR`；
`engine/movement.py:46` `_MARCH_LOSS_CAP_PCT` 0.30、`:48` `_TERRAIN_RES` 8。

### 補給（`engine/logistics.py`）
`RESUPPLY_RANGE_KM` 2.0、`_DEFAULT_RATE` 200.0、`_DEFAULT_BASIC_LOAD` 100.0。

### 偵測（`intel/sensor.py`、`engine/sensor_wiring.py`）
`_IDENTIFY_THRESHOLD` 0.85、`_CLASSIFY_THRESHOLD` 0.55、`ERROR_RADIUS_M`（500/200/50）、
`INTRINSIC_OPTICAL`（內建目視 4km，沿用 `SEED_SENSORS["EO_DAY"]`）、`_OBS_HEIGHT_M` 10.0。

### 交戰（`engine/engage_wiring.py`、`ai_loop/context.py`）
`_ENGAGE_OBS_M` 10.0、`_DEGRADED_HEALTH` 50.0。

### 天氣效應（`modules/weather/weather/effects.py`）
`_RF_DB_PER_MMHR` 0.5、`_MOBILITY_RAIN_FULL_MMHR` 50 / `_MOBILITY_FLOOR` 0.4、
`_IR_RAIN_FULL_MMHR` 60 / `_IR_FLOOR` 0.3、`_OPTICAL_FLOOR` 0.1、
`_VISIBILITY_FULL_M` 10000、`_UAV_MAX_WIND_MS` 12、`_UAV_MAX_PRECIP_MMHR` 25。

### 通聯（`modules/comms/comms/link_budget.py`）
`ONLINE_MARGIN_DB` 6.0、`DEGRADED_MARGIN_DB` 0.0。

### 韌性/逾時（`plugins/terrain_client.py`、`weather_client.py`、`comms_client.py`）
`_DEFAULT_CALL_DEADLINE_S` 0.2、`_DEFAULT_HEALTH_INTERVAL_S` 10.0、
`_DEFAULT_HEALTH_THRESHOLD` 3、`_DEFAULT_BREAKER_THRESHOLD` 5、`_DEFAULT_BREAKER_COOLDOWN_S` 5.0。

### 串流（`state/broadcaster.py`）
`RING_CAPACITY` 5000、`_CLOCK_EVERY_TICKS` 5；前端 `MAX_EVENTS` 1000。

---

## 建議的分期（供決定）

- **P1（有感、風險低）**：把「兵推行為」那群（移動/補給/偵測/交戰）改成從 DB 單例讀，
  缺值退現有常數 → 系統設定頁開一個「推演參數」分頁。**這群改了會影響裁決結果**，
  故需同時確認 golden replay 的處理方式（預設值不變 → golden 不受影響）。
- **P2（觀測性）**：韌性/逾時、串流那群搬進設定頁（唯讀顯示 → 再開放編輯）。
- **P3（唯讀即可）**：C 層的 env 一律唯讀顯示 + 標「需重啟 X 服務」，不在 UI 開放編輯
  （改 env 本來就要動部署，UI 改了也無處落地）。

## 尚待確認（實作前需釐清）
1. **改了會影響裁決的參數，是否該全域生效？** 進行中的推演局若中途改速度/耗損，
   等於改變物理規則。可能較合理的是：新局才套用，或改為 per-session 覆寫。
2. **golden replay**：只要預設值不變就不受影響；但若使用者改了全域值再跑 golden，
   會失敗。是否要讓 golden 固定用預設值（忽略 DB 設定）？
