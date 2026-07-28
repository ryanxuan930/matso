-- #31 推演局封存：WargameSession 新增封存時間（歷史/刪除頁）
-- AlterTable
ALTER TABLE `WargameSession` ADD COLUMN `archivedAt` DATETIME(3) NULL;
