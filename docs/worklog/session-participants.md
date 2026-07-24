---
task: "#57 每局參與者名冊（帳號↔陣營↔角色）"
status: DONE
started: 2026-07-24T10:00+08:00
updated: 2026-07-24T10:40+08:00
agent: Opus 4.8
---

# #57 每局參與者名冊——指派哪些帳號可操控/查看哪個陣營

## 目標摘要
使用者問「怎麼指定各推演裡哪些帳號可操控或查看哪個陣營」。盤點發現：`SessionParticipant`
（帳號×推演×陣營×角色）資料表 + 所有權限（fog-of-war 過濾、下令權限、視角）**早已在用它**，
但**沒有任何 UI/API 能指派參與者**——建局只把建立者加成統裁。本卡補上缺口。

## 設計決策
- **指派＝導演級動作**：名冊即 fog-of-war 與下令權限來源（SPEC §12），故限**全知**（統裁/白軍/管理）
  或本局統裁/白軍參與者。
- **UI 放 lobby 每局面板**（使用者選）：每局列加 👥 鈕 → 名冊 modal（列出/改陣營/改角色/移除 + 新增）。
- **可指派陣營＝本局單位陣營 + WHITE_CELL**（白軍/分析/統裁用 WHITE_CELL；指揮官/參謀/觀察員用交戰陣營）。
- **角色語意**：COMMANDER/STAFF＝可操控該陣營；OBSERVER＝只查看；WHITE_CELL_STAFF/EXERCISE_DIRECTOR＝全知。
- **upsert**（唯一約束 userId+sessionId）；**不可移除最後一位統裁**（避免整局無人可管理）。
- 契約先行（core_api.yaml）→ 驗證 → 實作（紅線 4）。**無 DB 變更**（沿用既有 SessionParticipant 表）。

## 檔案異動
| 檔案 | 動作 | 說明 |
|------|------|------|
| contracts/core_api.yaml | 改 | +SessionParticipantView / ParticipantRoster / AssignParticipantRequest schema；+GET participants、PUT/DELETE participants/{userId} |
| core/app/api/participants.py | 新增 | router + 服務：list（名冊+可指派陣營）/assign（upsert，陣營+帳號驗證）/remove（守最後統裁）；限導演 |
| core/app/api/__init__.py、main.py | 改 | 註冊 participants_router |
| core/tests/unit/test_participants_api.py | 新增 | 7 tests（名冊/指揮官擋/指派+upsert+移除/非本局陣營 422/未知帳號 404/WHITE_CELL OK/守最後統裁 403） |
| platform/app/composables/useParticipants.ts | 新增 | fetchRoster/fetchAllUsers/assign/remove + 角色中文 |
| platform/app/pages/lobby.vue | 改 | 每局 👥 鈕 + 名冊 modal（列/改陣營/改角色/移除 + 選帳號+陣營+角色指派）；限統裁/管理 |
| platform/app/types/api.ts | 生成 | gen:api |

## 測試證據
- test_participants_api 7 passed；core unit **724 passed**；mypy 193 Success；ruff All checks passed。
- contracts openapi 驗證通過；前端 lint/typecheck 綠；core 容器重建含新端點。

## 完成 / 後續可強化
- **完成**：統裁於 lobby 每局 👥 → 指派任一帳號到某陣營+角色 → 該帳號登入即以該陣營視角/權限參與；
  可改陣營/角色/移除。查看他陣營視角原本就能（白軍控制台 as_faction 視角切換）。
- **後續可強化**：建局精靈直接分配參與者；unit_scope（限指揮特定單位子集）UI；批次指派；名冊寫入 Ledger 稽核。
