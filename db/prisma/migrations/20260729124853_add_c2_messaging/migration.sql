-- CreateTable
CREATE TABLE `Message` (
    `id` VARCHAR(191) NOT NULL,
    `sessionId` VARCHAR(191) NOT NULL,
    `kind` ENUM('FREE_TEXT', 'REQUEST', 'APPROVAL', 'REPORT') NOT NULL,
    `fromUserId` VARCHAR(191) NOT NULL,
    `fromSeat` ENUM('COMMANDER', 'S2_INTEL', 'S3_OPS', 'FSO_FIRES', 'S4_LOG', 'OBSERVER') NULL,
    `toSeat` ENUM('COMMANDER', 'S2_INTEL', 'S3_OPS', 'FSO_FIRES', 'S4_LOG', 'OBSERVER') NULL,
    `toFaction` VARCHAR(191) NOT NULL,
    `refId` VARCHAR(191) NULL,
    `body` TEXT NOT NULL,
    `tick` INTEGER NOT NULL,
    `readAt` DATETIME(3) NULL,
    `createdAt` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `Message_sessionId_toFaction_idx`(`sessionId`, `toFaction`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `Request` (
    `id` VARCHAR(191) NOT NULL,
    `sessionId` VARCHAR(191) NOT NULL,
    `faction` VARCHAR(191) NOT NULL,
    `kind` ENUM('AIR_RECON', 'FIRE_SUPPORT', 'RESUPPLY_VOUCHER') NOT NULL,
    `status` ENUM('PENDING', 'APPROVED', 'DENIED', 'EXPENDED') NOT NULL DEFAULT 'PENDING',
    `params` JSON NOT NULL,
    `requestedById` VARCHAR(191) NOT NULL,
    `requestedSeat` ENUM('COMMANDER', 'S2_INTEL', 'S3_OPS', 'FSO_FIRES', 'S4_LOG', 'OBSERVER') NULL,
    `requestedAtTick` INTEGER NOT NULL,
    `decidedById` VARCHAR(191) NULL,
    `decidedAtTick` INTEGER NULL,
    `decisionNote` TEXT NULL,
    `createdAt` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `Request_sessionId_faction_idx`(`sessionId`, `faction`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
