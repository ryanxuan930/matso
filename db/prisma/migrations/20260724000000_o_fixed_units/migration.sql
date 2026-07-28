-- 固定單位（指揮部等）：TacticalUnit 新增 isFixed 旗標（不接受 MOVE 令，劇本 ORBAT 設定）。
-- AlterTable
ALTER TABLE `TacticalUnit` ADD COLUMN `isFixed` BOOLEAN NOT NULL DEFAULT false;
