-- AlterTable
-- 本局的勝負條件。想定 schema 一直有 `victory_conditions`、loader 讀得進 LoadedScenario、
-- 還做了語意驗證（`_validate_victory`）、劇本編輯器也有完整的編輯區——
-- **就是沒有任何一條路把它帶進執行期**。`resolve_victory_conditions` 只讀 Redis 的
-- ai_config，而 `AutonomyConfig` 根本沒有 `victory` 欄位（pydantic 會丟掉未宣告欄位），
-- 於是每一局的判定都是「最後存活」，`scenario/triggers.check_victory()` 零生產路徑。
--
-- NULL＝未宣告＝沿用「最後存活」（既有局零影響）。
ALTER TABLE `WargameSession`
  ADD COLUMN `victoryConditions` JSON NULL;
