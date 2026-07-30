---
task: V2.1 WP-E2
status: DONE
started: 2026-07-30T00:00+08:00
updated: 2026-07-30T00:00+08:00
agent: Opus 5
---

# WP-E2 認證強化

## 目標摘要

盤點欠帳打包：refresh token 輪替＋撤銷表（**登出真的生效**）、帳號鎖定、`needs_rehash` 接線、
JWT secret 生產強制。

## 開工前查證：四項裡有一項已經做完了

**JWT secret 生產強制早就有且真的有被呼叫**——`Settings.ensure_production_safe()` 涵蓋
預設 secret / `STUB_GATEWAY` / CORS 萬用字元，而且 `main.py:47` 確實呼叫了它。
（這次不是「寫好沒接」——我特地查過呼叫端才敢說。）剩下三項是真的缺。

## 三個修正

**1. 登出在此之前是 no-op。** `POST /auth/logout` 的註解自己寫著「無狀態 JWT：伺服器不維護
黑名單（Phase 1）；登出即用戶端丟棄 token」——於是**撿到 refresh token 的人照樣能一直換發
新的 access**。現在收 refresh token 並寫進撤銷表。

**2. 輪替 + 重用偵測。** 換發時撤銷舊的那張；若有人拿**已撤銷**的 refresh 來換，
那代表 token 被複製過（合法持有者早就換掉它了）→ 撤銷該使用者已知的全部並拒絕。
**這是 rotation 真正的價值**：把「偷到 token」從「可以無限續期」變成
「最多用一次，而且會被發現」。

**3. 帳號鎖定 + 雜湊升級。** 5 次失敗鎖 15 分鐘；`needs_rehash` 一直存在但**沒有任何呼叫端**
（參數升級後既有密碼永遠停在舊參數），現在在登入成功後升級——那是唯一拿得到明文的時機。

## 檔案異動

| 檔案 | 動作 | 說明 |
|------|------|------|
| core/app/auth/tokens.py | 修改 | `jti` + `expires_at` 進 claims（撤銷的最小單位） |
| core/app/auth/service.py | 修改 | `logout()`、輪替、重用偵測、鎖定、雜湊升級 |
| core/app/api/auth.py | 修改 | logout 收 refresh token（**不再是 no-op**） |
| core/app/models/tables.py、db/prisma | 修改 | `RevokedToken` 表 + `User.failedAttempts/lockedUntil`（migration `20260730160000_e2_auth_hardening`） |
| contracts/core_api.yaml | 修改 | logout 的 requestBody |
| platform/app/stores/auth.ts、pages/lobby.vue | 修改 | 前端登出先撤銷再清本地（且 `await`） |
| core/tests/unit/test_auth_hardening.py | 新增 | 12 條 |

## 測試證據

- `uv run pytest -q -m "not benchmark"` → **1921 passed, 8 skipped, 4 deselected**
- ruff / mypy(264) / schema-sync(24 tables / 232 columns) / 前端兩閘門 → clean
- 活 DB：先 `mariadb-dump` 備份（77.4 MB）→ `migrate:deploy`
- 突變測試 6 個全數被抓

## 決策與陷阱

**鎖定時回的錯誤與密碼錯誤完全一樣。** 分開回會把「這個帳號存在」洩漏出去，
那正是防帳號列舉在擋的事。鎖定期間也跑一次 `dummy_verify()`，讓耗時與正常路徑一致。

**鎖了就重設計數**，否則解鎖後**一次**失敗又立刻鎖回去。

**沒有 `jti` 的舊 token 略過撤銷但仍有效。** 簽發時還沒有那個欄位，強制失效會在部署當下
把所有人踢掉。⚠ 這條的測試被突變測試修正過：只驗「第一次不拋」殺不掉「把空 jti 也寫進
撤銷表」的突變——那會在**第二次**才把所有舊 token 一起擋掉。要驗到得斷言撤銷表裡
沒有空 jti 的列，並且**再 refresh 一次**。

**`_revoke_all` 只能撤銷撤銷表裡有的**（＝曾被輪替過的）——還在流通、從未被換過的那些
沒有紀錄。完整的「全家族撤銷」需要簽發時就登記每一張，那是更大的改動；此處先讓重用偵測
至少切斷已知的鏈，並留下 `REUSE_DETECTED` 供稽核追查。**這個限制寫在程式註解裡**，
不留在只有我知道的地方。

**前端登出撤銷失敗仍照清本地**——使用者按了登出就該登出，後端連不上不該把他困在
已登入狀態。

## 中斷續作指引

- **下一步第一件事**：E4 監控落地（需要使用者決定 compose 的埠與映像）、G3/G4。
- **未竟項**：
  1. **完整的 token 家族撤銷**（見上；需要簽發時登記每一張 refresh token）。
  2. **撤銷表沒有清理排程**——過期的列可安全刪除但目前沒人刪。
  3. **鎖定門檻/時長寫死在程式**（`LOCKOUT_THRESHOLD`/`LOCKOUT_MINUTES`），未進 SimParams 或設定。
  4. 前端沒有「帳號已鎖定」的專門提示（後端刻意回同一個錯誤，所以前端也無從分辨——那是設計）。
