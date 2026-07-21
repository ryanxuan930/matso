# CODE_REVIEW — M4/M5/O6.1 程式碼審查（2026-07-21）

> 範圍：branch `feat/o6.1-role-manager`（HEAD `a8ea3a7`）。聚焦 **O1.7 review（R1–R10，已修復）
> 之後未經審查的程式碼**——M4 全端（O4.1–O4.6：auth/lobby/WS stream/orders/units + platform
> stores）與 O6.1（AI 推論地基）。方法：逐檔精讀安全/併發/授權敏感路徑，每條 finding 附觸發情境
> 與建議修法。M0–M3 核心（SimClock/Ledger/Kernel/adjudication）本輪不重複。
>
> 本檔為「發現清單」，非任務卡；修復請開卡或記入 PROGRESS.md Backlog（守紅線 §5：不順手修）。

---

## 🔴 高（授權/資料正確性，建議儘早修）

### C1 — 跨陣營取消指令：cancel 無擁有者/faction 授權檢查
[core/app/orders/service.py:83](core/app/orders/service.py#L83)、[core/app/api/orders.py:80](core/app/api/orders.py#L80)
`cancel()` 只驗 `order.session_id == session_id`；API 層 `require_participant` 只驗「是本 session
參與者」。**觸發**：RED 參與者取得 BLUE 的 order_id → `DELETE /sessions/{id}/orders/{oid}` →
成功取消敵方待執行指令。order_id 雖是 UUID，但授權不得依賴不可猜性；且 id 會經 WS 事件 payload
等面流出。GET /orders 有 faction 過濾、DELETE 卻沒有——同資源不對稱。
**修法**：cancel 時 join `TacticalUnit.faction` 比對呼叫者 faction（omniscient 放行），或限
「issuer 本人 + 全知」可取消；補「跨陣營取消 → 403/404」contract test。

### C2 — WS 訂閱間隙掉事件：先讀 ring 再訂閱 pub-sub
[core/app/api/ws.py:93-118](core/app/api/ws.py#L93)
`_run_stream` 順序：`_read_ring`（lrange 快照）→ 送 backfill → `_pump_live` 才 `subscribe`。
**觸發**：在 lrange 與 subscribe 生效之間發佈的事件**永久漏失**——不在快照、也收不到 pub-sub；
client 以為已 live，且缺口無感（last_seq 由後續事件接續更新，不會觸發 RESYNC）。高事件率時必現，
症狀是「偶發少一則事件」極難重現。
**修法**：反轉順序——先 subscribe（開始緩衝 live）→ 再讀 ring 送 backfill → live 迴圈以
`seq ≤ 已補送最大 seq` 去重後放行。補一個 fakeredis 競態測試：backfill 期間 publish 一則，斷言不掉。

### C3 — broadcast seq 指派與 ring 寫入非原子：雙寫入者順序倒置（MEDIUM-HIGH）
[core/app/stream/publish.py:33-40](core/app/stream/publish.py#L33)
`INCR` 與 `rpush` 分兩步；O4.6 起 **API 層 publish_event 與 Kernel RedisBroadcaster 是兩個併發
寫入者**（O1.4 single-writer 前提已被打破）。
**觸發**：A 取 seq=5、B 取 seq=6，B 先 rpush → ring 順序 [6,5] → backfill 依 ring 序送出 →
client last_seq 終值 5（其實已看過 6）→ 重連要求 >5 → 重收 6（重覆）。另：INCR 成功但 rpush 前
崩潰 → 永久 seq 缺口，範圍檢查會誤判。
**修法**：seq+rpush+publish 包成 **Lua script** 原子執行；或 API 側事件改走 Kernel 單寫入者代發
路徑（publish_event 明文限 stub 模式）。client 端 `lastSeq` 更新取 `max(...)`。

---

## 🟡 中（安全強化/穩健性）

### C4 — 登入計時側信道，部分抵消帳號列舉防護
[core/app/auth/service.py:28-30](core/app/auth/service.py#L28)
`user is None or not verify_password(...)` 短路：帳號**不存在**時跳過 Argon2（毫秒級回應）、
存在時跑 Argon2（數十~百 ms）。錯誤訊息相同但**時間差可列舉帳號**——與 docstring 宣稱的
列舉防護相違（SPEC §12 明列此需求）。
**修法**：user 為 None 時對固定 dummy Argon2 hash 跑一次 verify 吸收時間差，再統一回錯。

### C5 — refresh token 無撤銷/輪替
[core/app/auth/service.py:33](core/app/auth/service.py#L33)
logout 不使 refresh 失效；refresh 有效 14 天、無 jti/denylist、無 rotation；帳號「停用」概念
不存在（`refresh()` 只查 user 存在）。refresh 一旦外洩，14 天內無法收回。
**修法**：列為 O7.5（RBAC 完整化）明確範圍：rotation + 撤銷表；現階段記入 SPEC §12 掛帳。

### C6 — WS HELLO `last_seq` 未驗型別 → 壞 client 一發炸掉 handler
[core/app/api/ws.py:89-90](core/app/api/ws.py#L89)
`hello.get("last_seq")` 未驗型別直接進 `plan_resume`。傳 `{"last_seq":"5"}`（字串）→
`last_seq > ring_max` 丟 TypeError → endpoint 例外、連線無 close code 直斷 + 每次一條 traceback。
**修法**：`isinstance(last_seq, int) and not isinstance(last_seq, bool)` 收斂，非法值當 None。

### C7 — 前端重連可長出多條併行 WS + events 無上限成長
[platform/app/stores/sessionStream.ts:35-61](platform/app/stores/sessionStream.ts#L35)、[:82-86](platform/app/stores/sessionStream.ts#L82)
(a) `scheduleReconnect` 覆寫 `reconnectTimer` 不清舊 timer、`connect()`/`open()` 不先關既有
socket——斷線抖動下可同時掛兩條 WS，事件雙倍。(b) `events.push` 無上限，長 session 記憶體
持續成長（後端 ring 有 5000 上限、前端沒有）。
**修法**：open 前 `ws?.close()` + `clearTimeout`；events 保留最近 N 則（如 1000）。

### C8 — 任何角色可建局並自任 EXERCISE_DIRECTOR（session 內全知）
[core/app/lobby/service.py:47-67](core/app/lobby/service.py#L47)
`create_session` 無角色檢查：PLAYER 也能建局並成為該局 WHITE_CELL/EXERCISE_DIRECTOR
（omniscient——見所有陣營單位/指令）。自己建的局尚屬合理，但與 §12 角色模型（誰能開演習）
未對齊，屬 O7.5 前的已知敞口——需要明確記帳，避免被誤當「已完成的權限模型」。

---

## 🔵 低（改善/防禦性）

### C9 — RoleManager 佇列無上限（與全系統背壓原則不一致）
[ai/matso_ai/inference/role_manager.py:71-75](ai/matso_ai/inference/role_manager.py#L71)（O6.1）
他處緩衝皆有界（BoundedSender 1000 → 4408），此處 `enqueue` 無界。O6.5 事件驅動 OPFOR 迴路
暴衝時佇列可無限成長。**修法**：加 `maxsize` + 滿載策略（拒收並拋錯）+ 供 `ai_queue_depth` 觀測（§20.3）。

### C10 — RoleManager.process_pending 部分失敗語義未定義
[ai/matso_ai/inference/role_manager.py:77-88](ai/matso_ai/inference/role_manager.py#L77)
批次中某筆 `_invoke` 拋例外 → 整批中斷、`_queue` 未清、已成功者卻已記 AIInvocationLog；重試會重覆
呼叫已完成者。**修法**：per-request 收斂為結果（成功/失敗），佇列一律清空。

### C11 — `_emit_adjudication_event` 每筆下令新建 redis 連線 + 全靜默失敗
[core/app/api/orders.py:42-51](core/app/api/orders.py#L42)
stub-only，但 `suppress(redis.RedisError)` 讓 E2E 偶發缺事件無 log 可查。**修法**：失敗 `_LOG.warning`。

### C12 — faction 過濾在應用層記憶體做（非 SQL WHERE）
[core/app/api/units.py:53-59](core/app/api/units.py#L53)、[core/app/orders/service.py:65-81](core/app/orders/service.py#L65)
先撈整個 session 的列再於 Python 丟棄敵方——輸出正確但少一層 DB 級縱深、大場景多做工。
**修法**：非全知者把 faction 直接進 WHERE。

### C13 — 預設 JWT secret / STUB_GATEWAY 僅告警仍可啟動
[core/app/config.py:24](core/app/config.py#L24)、[core/app/main.py:23](core/app/main.py#L23)
正式部署漏設 `JWT_SECRET` → 可預測金鑰上線；`STUB_GATEWAY=1` 誤設 → 物理預檢恆真。
**修法**：`MATSO_ENV=production` 時兩者 fail-fast（紅線 3「護欄不可 bypass」的部署面延伸）。

### C14 — CORS `allow_credentials=True` + 萬用字元的誤設風險
[core/app/main.py:29-35](core/app/main.py#L29)
origins 目前為明確清單（正確），但 `CORS_ORIGINS=*` 會得到 `["*"]` + credentials——瀏覽器拒且屬誤區。
**修法**：`cors_origin_list` 遇 `*` 時關掉 credentials（或啟動拒絕）。

### C15 — `_derive_seed(name, user_id)` 同名同人 → 同 master_seed
[core/app/lobby/service.py:91-97](core/app/lobby/service.py#L91)
同使用者用同名建兩局 → RNG 流完全相同（對手隨機性可預測）。**修法**：摻入 `session.id`（uuid）再導出。

### C16 — `Settings()` 多處各自實例化
[core/app/api/deps.py:65](core/app/api/deps.py#L65)、[core/app/main.py:20](core/app/main.py#L20)
env 重複讀取、易生隱性不一致。**修法**：統一走 `get_settings()`（已 lru_cache）。

---

## 🟢 記帳（超出 code fix 範圍，歸 O7.5 RBAC）

### C5 — refresh token 無撤銷/輪替
[core/app/auth/service.py:33](core/app/auth/service.py#L33)
logout 不使 refresh 失效；無 jti/denylist/rotation。需 DB 撤銷表（prisma migration）+ RBAC 設計，
**歸 O7.5**，不在本輪 code fix（避免順手加 migration，守 ADR 004）。

### C8 — 任何角色可建局並自任 EXERCISE_DIRECTOR
[core/app/lobby/service.py:47-67](core/app/lobby/service.py#L47)
需完整角色×端點存取矩陣（O7.5）才好定「誰能開演習」。本輪不加半套 gate（恐破壞 seed/E2E 流程），
**歸 O7.5**。

### C_URL — WS token 走 URL query string
[core/app/api/ws.py:50-54](core/app/api/ws.py#L50)
瀏覽器 WS 無法設 header 是真約束；短 TTL 已緩解。屬協定/部署層（access log 遮罩 `token=`、
長期改一次性 ticket），非單點 code fix。列 §16.2 註記。

---

## ⚪ Nit
- [core/app/api/orders.py:32](core/app/api/orders.py#L32) `_participant = require_participant` 別名無增益 → 直接用原名。
- `OpenAICompatibleClient.complete` 冒 httpx 原生例外（未包領域錯誤）——O6.2 接護欄時統一。

---

## 本輪確認良好（未發現問題）
- `plan_resume`（stream/backfill.py）純函數 + **範圍檢查**（非差值），空 ring / last_seq=0 / seq 倒退 / trim 缺口全對（守 O1.7/R7）。
- `BoundedSender` 非阻塞 offer + 溢出 4408 語義正確。
- Argon2id 雜湊、token type 檢查（refresh 不可當 access）、錯誤訊息層面的列舉防護（唯計時見 C4）。
- fog-of-war faction 過濾一律後端強制、未見 bypass；units/orders/WS/intel 全走 `require_participant`/`is_visible`。
- O6.1：latency 注入時鐘、只入 side log 不進 Ledger hash（守 R8）；OPFOR 佇列優先 + adapter 攤銷正確。

---

## 修復進度（2026-07-21，branch `fix/code-review-2026-07-21`）
全關卡綠：544 passed / mypy 123 clean / ruff / schema-sync / 前端 lint+typecheck。

| # | 嚴重度 | 狀態 | 修法 / 回歸測試 |
|---|--------|------|------|
| C1 跨陣營取消 | 🔴 | ✅ | cancel 加 faction 授權（他陣營回 404）；`test_cancel_cross_faction_denied_as_not_found` |
| C2 WS 訂閱間隙 | 🔴 | ✅ | 先 subscribe 再讀 ring + seq 去重（`_run_stream`/`_pump_live` 重構） |
| C3 seq 非原子 | 🟡+ | ✅ | `redis_stream.publish_to_stream` Lua 原子（fakeredis 退步）；broadcaster+publish_event 共用；前端 lastSeq=max |
| C4 登入計時 | 🟡 | ✅ | `dummy_verify()` 吸收時間差 |
| C6 last_seq 型別 | 🟡 | ✅ | `_parse_last_seq` 收斂；`test_parse_last_seq_rejects_non_int` |
| C7 前端多 WS/無界 events | 🟡 | ✅ | open 前關舊 socket/清 timer；events 上限 1000 |
| C9 RoleManager 無界佇列 | 🔵 | ✅ | `max_queue`+`QueueFullError`；`test_queue_full_raises_backpressure` |
| C10 批次部分失敗 | 🔵 | ✅ | per-request 隔離+佇列必清；`test_partial_failure_isolated_and_queue_cleared` |
| C11 emit 靜默失敗 | 🔵 | ✅ | 失敗 `_LOG.warning` |
| C12 faction SQL 過濾 | 🔵 | ✅ | orders/units 過濾下推 SQL WHERE |
| C13 prod fail-fast | 🔵 | ✅ | `ensure_production_safe()`；`test_config_safety.py` |
| C14 CORS 萬用字元 | 🔵 | ✅ | `cors_allows_wildcard` → 停用 credentials + prod 拒啟動 |
| C15 seed 碰撞 | 🔵 | ✅ | `_derive_seed` 摻入 session.id |
| C16 Settings 統一 | 🔵 | ✅ | `_default_channel` 走 `get_settings()` |
| nit `_participant` 別名 | ⚪ | ✅ | 移除，直接用 `require_participant` |
| C5 refresh 撤銷 | 🟡 | ⏸ 歸 O7.5 | 需 DB 撤銷表（prisma migration）+ RBAC |
| C8 建局角色 | 🟡 | ⏸ 歸 O7.5 | 需完整角色×端點存取矩陣 |
| C_URL WS token | 🔵 | ⏸ 部署層 | log 遮罩 / 一次性 ticket；瀏覽器 WS 無法設 header |OR 迴路
暴衝（每 tick 都觸發）時佇列無限成長，且低優先角色（AAR）會被 OPFOR 永遠餓死而不被發現。
**修法**：加 `maxsize` + 滿載策略（拒收或丟最舊低優先者）+ 佇列深度 metric（呼應 SPEC §20.3
`ai_queue_depth`）。O6.5 接迴路前處理即可。

### C10 — RoleManager.process_pending 部分失敗語義未定義
[ai/matso_ai/inference/role_manager.py:77-88](ai/matso_ai/inference/role_manager.py#L77)（O6.1）
批次中某筆 `_invoke` 拋例外（LLM 逾時/連線斷）→ 整批中斷、`_queue` **未清**、但已成功的幾筆
已記 AIInvocationLog——呼叫端重試會**重覆呼叫已完成者**（log 重覆、token 雙倍）。
**修法**：per-request try/except 收斂為失敗結果（AIResult 加 error 欄），佇列一律清空；
或 docstring 明文「拋出時佇列保留、可能部分已執行」讓呼叫端自行冪等。

### C11 — `_emit_adjudication_event` 每筆下令新建 Redis 連線 + 失敗全靜默
[core/app/api/orders.py:42-51](core/app/api/orders.py#L42)
`redis.from_url` 每筆訂單建新 client；`contextlib.suppress(redis.RedisError)` 把 publish 失敗
全吞掉無 log。僅 stub 模式路徑（正式關閉），但 E2E 偶發缺事件時完全無跡可查。
**修法**：復用 app 級 client；失敗至少 `_LOG.warning`。

### C12 — `Settings()` 多處各自實例化
[core/app/main.py:20](core/app/main.py#L20)、[core/app/api/deps.py:64-65](core/app/api/deps.py#L64)
main 模組層、`_default_channel`、`get_settings()`（lru_cache）三條路徑各建一次——env 於不同
時點讀取，測試 override `get_settings` 時前兩者不受影響，易生「讀到舊值」的隱性不一致。
**修法**：統一走 `get_settings()`。

### C13 — faction 過濾在 Python 記憶體做（非 SQL WHERE）
[core/app/api/units.py:53-59](core/app/api/units.py#L53)、[core/app/orders/service.py:65-81](core/app/orders/service.py#L65)
先撈整個 session 的單位/訂單再於應用層過濾。輸出正確（無洩漏），但敵方列仍載入行程記憶體——
大場景（500+ 單位）多做工，且少一層 DB 級縱深防禦。
**修法**：非全知者把 `faction = :faction` 下推到 WHERE（omniscient 才免）。

### C14 — 預設 JWT secret / STUB_GATEWAY 於正式部署僅告警不阻擋
[core/app/config.py:24](core/app/config.py#L24)、[core/app/main.py:23-26](core/app/main.py#L23)
漏設 `JWT_SECRET`
