-- #30 squad 火力容量：EquipmentInstance 新增建制數量（一件 instance 代表 N 件同型裝備）
-- AlterTable
ALTER TABLE `EquipmentInstance` ADD COLUMN `quantity` INTEGER NOT NULL DEFAULT 1;
