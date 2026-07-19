/*
  Warnings:

  - Added the required column `ledgerSeq` to the `SimCheckpoint` table without a default value. This is not possible if the table is not empty.

*/
-- AlterTable
ALTER TABLE `SimCheckpoint` ADD COLUMN `ledgerSeq` INTEGER NOT NULL;

-- AlterTable
ALTER TABLE `TacticalEventLog` ADD COLUMN `detail` JSON NULL;
