---
task: "#98 陣營關係矩陣持久化（#91 的前置）"
status: DONE
started: 2026-07-28T15:20+08:00
updated: 2026-07-28T16:10+08:00
agent: Opus 5
spec: SPEC_FULL §12.1（關係矩陣）、ADR 006
---

# #98 關係矩陣持久化：讓「友軍」這個概念在執行期活著

## 問題
`FactionRelations`（三值、對稱、未宣告＝HOSTILE）**早就寫好**，scenario loader 也會建，
但**建完就丟**——`WargameSession` 沒有欄位存它。後果：

- `sim_runtime` 的偵測 sweep 拿不到關係 → 盟軍互相偵測成 contact。
- `ai_loop/orchestrator.py:159` 寫死 `FactionRelations()`（自己註明「之後由 scenario relations 注入」）
  → **AI 會把盟軍當敵人打**。
- 前端無從得知關係 → `cop.vue` 硬寫 `relation: 'HOSTILE'`（#91 卡在這）。

## DB 變更（謹慎執行的紀錄）
使用者要求「小心進行」，故按下列順序：

1. **先備份**：`mariadb-dump` 全庫 57MB，並記下基線 **3 sessions / 44 units / 50 equipment**。
2. **`prisma migrate status`** 確認無 drift、只有一筆待套用。
3. 用 **`migrate:deploy`**（只套用待處理遷移、**永不 reset**）而非 `migrate dev`。
4. 遷移後逐項核對：欄位存在、筆數仍為 3/44/50、既有三局的新欄位皆為 **NULL**。

```sql
ALTER TABLE `WargameSession` ADD COLUMN `factionRelations` JSON NULL;
```

**可為 NULL 是關鍵設計**：既有局維持 NULL ＝ 未宣告 ＝ 全 HOSTILE ＝ **與加欄位前語義完全相同**，
零資料遷移、零行為改變。有測試（`test_null_relations_means_all_hostile`）釘住這條不變式。

## 交付
| 檔案 | 動作 | 說明 |
|---|---|---|
| `db/prisma/schema.prisma` + migration | 修改/新增 | `WargameSession.factionRelations Json?` |
| `core/app/models/tables.py` | 修改 | 對應 `faction_relations` 欄位 |
| `core/app/factions/relations.py` | 修改 | +`to_triples()`（確定性排序）、+`relations_from_triples()`（**寬容解析**） |
| `core/app/factions/session_store.py` | 新增 | `load_session_relations(db, session_id)`——所有子系統的單一入口 |
| `core/app/scenario/loader.py` | 修改 | 開局時把想定宣告的關係寫入（空宣告存 None） |
| `core/app/lobby/service.py` | 修改 | 複製推演時一併複製關係（否則副本盟友憑空消失） |
| `core/app/sim_runtime.py` | 修改 | 載入該局關係 → 注入 `SensorSweepSystem` |
| `core/app/ai_loop/orchestrator.py` | 修改 | 去掉寫死的全 HOSTILE，改讀該局關係 |

## 設計決定
- **`session_store.py` 與 `relations.py` 分開**：後者是純模組（敵我語義的數學，不碰 DB），
  前者才碰 DB。所有需要「這一局的關係」的子系統都走同一入口，避免各自 query、各自退回不同預設。
- **解析刻意寬容**：None / 非陣列 / 長度錯 / 型別錯 / 未知關係字串 → 一律退回全 HOSTILE，
  且**一筆髒資料只跳過該筆**，不毀掉整個矩陣。理由：關係讀不出來不該讓整局跑不動，
  而「未宣告＝敵對」本就是既有語義。有 7 條參數化測試覆蓋。
- **`to_triples()` 排序固定**：否則同一份關係每次存出的 JSON 都不同，diff 與 replay 都會噪。

## 測試證據
- 新增 12 條測試；`uv run pytest` → **1079 passed / 8 skipped**（golden 6 未破）；
  ruff / mypy(203) / **schema-sync（16 表 **141** 欄，+1）** 全綠。
- **容器實測（複製局，驗完刪除）**——宣告 `BLUE ↔ YELLOW = ALLIED` 後：

  | 觀測方 | 宣告前 | 宣告後 |
  |---|---|---|
  | BLUE | RED 13、YELLOW 10 | **RED 13**（YELLOW 不再成為 contact） |
  | YELLOW | RED 13、BLUE 12 | **RED 13**（BLUE 不再成為 contact） |
  | RED | BLUE 12、YELLOW 10 | **BLUE 12、YELLOW 10**（敵對雙方照舊全看得到） |

- 收尾核對：測試局已刪、Redis 殘鍵 0、DB 仍 3 局且三局的 `factionRelations` 皆為 NULL
  （使用者原有資料未被本卡觸碰）。

## 未做（留給 #91）
- **API 尚未透出關係矩陣**：前端仍拿不到，`cop.vue` 的 `relation` 暫時還是硬寫 HOSTILE。
  這是 #91 的第一步（契約先行加端點），刻意不在本卡做，以免把兩張卡混在一起。
- 白軍局中宣戰/停火的 UI（`set_relation` 已能產 `FACTION_RELATION_CHANGED` 事件，但沒有入口）。

## 中斷續作指引
- **下一步第一件事**：#91 —— 先在 `core_api.yaml` 加關係矩陣端點（或併入 session 摘要），
  重生型別，再讓 `cop.vue` 依 `observerFaction`（#90 已備）對各陣營關係決定 2525 affiliation。
