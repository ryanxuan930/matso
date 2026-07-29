-- CreateTable
CREATE TABLE `FirePlan` (
    `id` VARCHAR(191) NOT NULL,
    `sessionId` VARCHAR(191) NOT NULL,
    `faction` VARCHAR(191) NOT NULL,
    `name` VARCHAR(191) NOT NULL,
    `status` ENUM('ACTIVE', 'CANCELLED') NOT NULL DEFAULT 'ACTIVE',
    `createdByParticipantId` VARCHAR(191) NULL,
    `createdAtTick` INTEGER NOT NULL,
    `createdAt` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `FirePlan_sessionId_faction_idx`(`sessionId`, `faction`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `FirePlanTarget` (
    `id` VARCHAR(191) NOT NULL,
    `planId` VARCHAR(191) NOT NULL,
    `seq` INTEGER NOT NULL,
    `label` VARCHAR(191) NULL,
    `targetLat` DOUBLE NOT NULL,
    `targetLng` DOUBLE NOT NULL,
    `rounds` INTEGER NOT NULL DEFAULT 4,
    `shooterUnitId` VARCHAR(191) NOT NULL,
    `schedule` ENUM('AT_TICK', 'ON_CALL') NOT NULL DEFAULT 'ON_CALL',
    `atTick` INTEGER NULL,
    `fireRequestId` VARCHAR(191) NULL,
    `status` ENUM('PENDING', 'FIRED', 'FAILED', 'SKIPPED') NOT NULL DEFAULT 'PENDING',
    `orderId` VARCHAR(191) NULL,
    `firedAtTick` INTEGER NULL,
    `failureReason` TEXT NULL,

    INDEX `FirePlanTarget_planId_seq_idx`(`planId`, `seq`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- AddForeignKey
ALTER TABLE `FirePlanTarget` ADD CONSTRAINT `FirePlanTarget_planId_fkey` FOREIGN KEY (`planId`) REFERENCES `FirePlan`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;
