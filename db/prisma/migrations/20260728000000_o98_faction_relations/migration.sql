-- #98 陣營關係矩陣持久化：WargameSession 新增 factionRelations（三元組 JSON 陣列）。
-- 可為 NULL：既有推演局維持 NULL ＝ 未宣告 ＝ 全 HOSTILE 預設（與過去語義相同，零資料遷移）。
-- AlterTable
ALTER TABLE `WargameSession` ADD COLUMN `factionRelations` JSON NULL;
