-- WP-B1 演習專案（Exercise）與生命週期。
--
-- 兩個新欄一律 NULL：**NULL ＝ 獨立局**，既有局零遷移（本 repo 每次加欄的house invariant）。
-- Exercise 與 WargameSession 之間刻意不設 FK——刪演習不該連坐刪局。

-- AlterTable
ALTER TABLE `WargameSession`
  ADD COLUMN `exerciseId` VARCHAR(191) NULL,
  ADD COLUMN `sessionRole` ENUM('REHEARSAL', 'MAIN', 'ANALYSIS') NULL;

-- CreateTable
CREATE TABLE `Exercise` (
    `id` VARCHAR(191) NOT NULL,
    `name` VARCHAR(191) NOT NULL,
    `phase` ENUM('PREP', 'REHEARSAL', 'EXECUTION', 'REVIEW', 'ARCHIVED') NOT NULL DEFAULT 'PREP',
    `scheduleJson` JSON NULL,
    `checklistJson` JSON NULL,
    `createdBy` VARCHAR(191) NOT NULL,
    `createdAt` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `phaseChangedAt` DATETIME(3) NULL,

    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `ExerciseAuditLog` (
    `id` VARCHAR(191) NOT NULL,
    `exerciseId` VARCHAR(191) NOT NULL,
    `at` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `actorId` VARCHAR(191) NOT NULL,
    `action` VARCHAR(191) NOT NULL,
    `fromPhase` ENUM('PREP', 'REHEARSAL', 'EXECUTION', 'REVIEW', 'ARCHIVED') NULL,
    `toPhase` ENUM('PREP', 'REHEARSAL', 'EXECUTION', 'REVIEW', 'ARCHIVED') NULL,
    `detail` JSON NULL,

    INDEX `ExerciseAuditLog_exerciseId_at_idx`(`exerciseId`, `at`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- AddForeignKey
ALTER TABLE `ExerciseAuditLog` ADD CONSTRAINT `ExerciseAuditLog_exerciseId_fkey` FOREIGN KEY (`exerciseId`) REFERENCES `Exercise`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;
