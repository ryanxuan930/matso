---
task: "WP-E3 /state 快照端點與 RESYNC 閉環"
status: DONE
started: 2026-07-29T22:10+08:00
updated: 2026-07-29T23:40+08:00
agent: Opus 5
spec: SPEC_V2.md §6 WP-E3（V2.0 路線第五張）；contracts/ws_protocol.md、SPEC_FULL §16.2
---

# WP-E3 /state 快照端點與 RESYNC 閉環

## 目標摘要
WS 協定的 `RESYNC_REQUIRED` 說「client 走 `GET /sessions/{id}/state` 全量重同步」，但那個端點
在契約裡只是一行佔位、**後端根本沒實作**；前端收到 RESYNC 後把結果丟掉，靠每 10 秒重抓
**六個獨立 GET** 兜底。六個回應彼此不同時，拼出的是「單位是新的、敵情是舊的」的畫面。

## 交付
| 檔案 | 動作 | 說明 |
|------|------|------|
| `contracts/core_api.yaml` | 修改 | `StateSnapshotView` + `/sessions/{id}/state` 補完（security/as_faction/schema/403） |
| `contracts/ws_protocol.md` | 修改 | 寫明 RESYNC 閉環的兩條 client MUST（原子重建、依 `last_seq` 去重） |
| `core/app/api/state.py` | **新增** | 端點本體。**不自行實作過濾**，直接呼叫四個既有 handler |
| `core/app/api/intel.py` | 修改 | 全知的**非參與者**不再 403（對齊 units/map-features/WS） |
| `core/app/state/redis_stream.py` | 修改 | `seq_key` / `ring_key` / `channel_key`——三個鍵原本散在 broadcaster/publish/ws 共 8 處字面值 |
| `core/app/{state/broadcaster,stream/publish,api/ws}.py` | 修改 | 改用上述 helper |
| `platform/app/stores/sessionStream.ts` | 修改 | `pullSnapshot()` + `snapshot` ref；RESYNC 走原子重建；STATE_DIFF 依 `last_seq` 去重；`connect(id, asFaction)` |
| `platform/app/pages/session/[id]/cop.vue` | 修改 | `applySnapshot()` + `watch(stream.snapshot)`；`refresh()` 由六請求改為「快照 + 指令 + session 摘要」；抽出 `applyFeatures()` |
| `core/tests/unit/test_state_snapshot.py` | **新增** | 12 條 |

## 設計決定

### 1. 快照**呼叫既有 handler**，不重寫過濾（本卡最關鍵的一項）
掃描發現三個端點的過濾規則**本來就不一致**：
- `/units` 以**全域 user.role** 判全知、非參與者的全知者放行、可見集＝自己＋**盟軍**、
  且有 `STUB_GATEWAY` 的 E2E affordance（回全單位）；
- `/intel` 以 **participant.role** 判、**無條件先 require_participant**、只回自己觀測到的；
- `/map-features` 以 user.role 判、可見集＝共同＋自己（**不含盟軍**）。

若快照另寫一份過濾，必然與其中某一條漂移，而**迷霧過濾的漂移就是資安漏洞**
（重連後看到的比正常時多＝洩漏，少＝誤殺）。故本端點直接呼叫四個 handler 函式
（`Depends(...)` 只是預設值，具名全傳即普通呼叫）——**一致性由構造保證**，
測試也就不寫死期望值，而是「同時打快照與端點再比對」。

### 2. 為了讓快照能一致，先讓端點彼此一致：`/intel` 的非參與者全知者
`/intel` 是唯一無條件 `require_participant` 的端點——一個**未加入該局的白軍觀察員**
在 units/map-features/WS 都看得到，在 intel 卻 403。這不是刻意設計（無測試釘住），
而快照要「與各端點逐項一致」就不可能同時滿足兩種語義。已把 `/intel` 對齊其餘三者。

### 3. `last_seq` **必須在讀狀態之前**取樣
反過來的話，介於「讀狀態」與「取 seq」之間送出的 STATE_DIFF 會**既不在快照裡、seq 又 ≤ last_seq**
——client 依約丟棄它，那個更新就永久遺失。先取 seq 的最壞情況只是「快照已含某 diff、
client 又套用一次」，而 diff 是覆寫式的，重複套用無害。有一條測試以呼叫順序釘住。

### 4. RESYNC 後 server **不會停止推播**
`pubsub.subscribe()` 在送 RESYNC **之前**就完成了。所以快照回來時可能已比某些 diff 舊。
前端因此在套用快照時清空 `unitPatches` 並以 `last_seq` 當去重基準，之後丟棄 `seq ≤ last_seq`
的 STATE_DIFF。少了任一半都會出現「單位跳回舊位置」。

### 5. 週期重取**保留節奏、拿掉拼裝**
規格說「去掉週期重抓兜底的 race」。race 的來源是**非原子**，不是「有週期」——而且
**指令列表沒有任何 WS 推播**，那個 timer 是它唯一的更新來源。故把 `refresh()` 從
六個獨立 GET 改成「一次原子快照 + 指令 + session 摘要」，節奏留著。

### 6. 白軍的視角要跟著進快照請求
`connect(id, asFaction)`：切到某軍視角後若重連仍抓**全知**快照，就是重連後看到的比正常時多。

## 測試證據
- 新增 12 條，核心是 4 組參數化的「快照 == 各端點」（BLUE 指揮官／白軍 god view／
  白軍 as_faction=RED／=BLUE，各比對 units/contacts/map_features/relations 四項），
  外加一條「過濾真的有東西可濾」（否則空集合對空集合也會綠）。
- **`uv run pytest` → 1244 passed / 8 skipped**；golden 6 未破；ruff/format/mypy(211)/
  OpenAPI 驗證/schema-sync 全綠；前端 `npm run lint` + `vue-tsc` 綠。
- 前端套用快照的三處**移除了防禦性 cast**，讓契約生成型別真的被檢查（不合就當場紅）。
- 容器實測：core/frontend 重建後 `/openapi.json` 確認端點存在、回應 schema 為
  `StateSnapshotView`（7 欄齊全）、`as_faction` 參數在；未帶 token → 401。

## 未做 / 已知限制
- 快照**不含 orders**：指令量大且與 STATE_DIFF 無關，仍走自己的端點（週期重取仍會抓）。
- `ws_protocol.md` 表列的 `INTEL_UPDATE` / `WEATHER_UPDATE` / `AI_TASK_UPDATE` / `ERROR`
  四種訊息型別**仍無任何實作**（掃描發現；本卡未動，記入 PROGRESS backlog）。
- `CLOCK` 的 payload 契約要求 `{tick, compression, session_state}`，實作是空物件（同上）。
- 背壓斷線（4408）契約說「斷線並要求全量重同步」，實作只斷線不送 RESYNC；重連時 `last_seq`
  通常仍在 ring 內 → 走 backfill，不會觸發快照（同上，記 backlog）。

## 中斷續作指引
- **本卡已全部完成並實測**。無未竟項。
- 後續相關：V2.0 剩 C5（comms 後果閉環）、G1（cop.vue 拆分）、D6.1（AAR 地圖重播）。
