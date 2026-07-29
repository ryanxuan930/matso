-- WP-B1：稽核軌跡加演習內單調序號。
--
-- `at` 的精度救不了同一個請求內連續寫入的兩筆（例如「勾稽 → 推階段」），
-- 而以 uuid 當 tiebreak 等於隨機排序——順序隨機的稽核軌跡讀不出因果。
-- 表是本次新增的（尚無資料），故可直接加 NOT NULL。
--
-- ⚠ 舊索引 `(exerciseId, at)` 不能先 DROP：它是 exerciseId 那條 FK 的支撐索引，
-- MySQL 會擋。順序必須是「先建新的 unique（同樣以 exerciseId 開頭，可頂替 FK）→ 再 DROP 舊的」。

ALTER TABLE `ExerciseAuditLog` ADD COLUMN `seq` INTEGER NOT NULL;
CREATE UNIQUE INDEX `ExerciseAuditLog_exerciseId_seq_key` ON `ExerciseAuditLog`(`exerciseId`, `seq`);
ALTER TABLE `ExerciseAuditLog` DROP INDEX `ExerciseAuditLog_exerciseId_at_idx`;
