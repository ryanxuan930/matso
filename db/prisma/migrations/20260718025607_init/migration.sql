-- CreateTable
CREATE TABLE `SystemConfiguration` (
    `id` VARCHAR(191) NOT NULL,
    `versionName` VARCHAR(191) NOT NULL,
    `simTickRateMs` INTEGER NOT NULL DEFAULT 1000,
    `globalRules` JSON NOT NULL,
    `integrationConfig` JSON NOT NULL,
    `updatedAt` DATETIME(3) NOT NULL,

    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `WargameSession` (
    `id` VARCHAR(191) NOT NULL,
    `name` VARCHAR(191) NOT NULL,
    `scenarioId` VARCHAR(191) NULL,
    `masterSeed` BIGINT NOT NULL,
    `mode` ENUM('REALTIME', 'WEGO', 'IGO_UGO') NOT NULL DEFAULT 'REALTIME',
    `startTime` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `endTime` DATETIME(3) NULL,
    `currentWeather` JSON NOT NULL,

    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `TacticalUnit` (
    `id` VARCHAR(191) NOT NULL,
    `sessionId` VARCHAR(191) NOT NULL,
    `designation` VARCHAR(191) NOT NULL,
    `unitLevel` ENUM('THEATER', 'CORPS', 'DIVISION', 'BRIGADE', 'BATTALION', 'COMPANY', 'PLATOON', 'SQUAD', 'FIRETEAM', 'INDIVIDUAL') NOT NULL,
    `faction` ENUM('BLUE', 'RED', 'WHITE_CELL', 'ALLIED') NOT NULL,
    `parentId` VARCHAR(191) NULL,
    `attributes` JSON NOT NULL,
    `currentLat` DOUBLE NULL,
    `currentLng` DOUBLE NULL,
    `elevation` DOUBLE NULL,
    `healthStatus` DOUBLE NOT NULL DEFAULT 100.0,
    `commsStatus` ENUM('ONLINE', 'DEGRADED', 'OFFLINE') NOT NULL DEFAULT 'ONLINE',

    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `EquipmentTemplate` (
    `id` VARCHAR(191) NOT NULL,
    `name` VARCHAR(191) NOT NULL,
    `category` VARCHAR(191) NOT NULL,
    `baseStats` JSON NOT NULL,

    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `EquipmentInstance` (
    `id` VARCHAR(191) NOT NULL,
    `templateId` VARCHAR(191) NOT NULL,
    `ownerId` VARCHAR(191) NOT NULL,
    `currentState` JSON NOT NULL,

    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `TacticalEventLog` (
    `id` VARCHAR(191) NOT NULL,
    `sessionId` VARCHAR(191) NOT NULL,
    `seq` INTEGER NOT NULL,
    `tick` INTEGER NOT NULL,
    `timestamp` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `eventType` VARCHAR(191) NOT NULL,
    `initiatorId` VARCHAR(191) NULL,
    `targetId` VARCHAR(191) NULL,
    `weatherSnapshot` JSON NOT NULL,
    `terrainModifier` DOUBLE NOT NULL,
    `reasoningChain` TEXT NULL,
    `aiDecision` JSON NOT NULL,
    `damageCalc` DOUBLE NULL,
    `prevHash` VARCHAR(191) NOT NULL,
    `selfHash` VARCHAR(191) NOT NULL,

    INDEX `TacticalEventLog_sessionId_timestamp_idx`(`sessionId`, `timestamp`),
    UNIQUE INDEX `TacticalEventLog_sessionId_seq_key`(`sessionId`, `seq`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `User` (
    `id` VARCHAR(191) NOT NULL,
    `username` VARCHAR(191) NOT NULL,
    `passwordHash` VARCHAR(191) NOT NULL,
    `totpSecret` VARCHAR(191) NULL,
    `role` ENUM('EXERCISE_DIRECTOR', 'WHITE_CELL_STAFF', 'COMMANDER', 'STAFF', 'OBSERVER', 'ANALYST', 'ADMIN') NOT NULL,
    `createdAt` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    UNIQUE INDEX `User_username_key`(`username`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `SessionParticipant` (
    `id` VARCHAR(191) NOT NULL,
    `userId` VARCHAR(191) NOT NULL,
    `sessionId` VARCHAR(191) NOT NULL,
    `faction` ENUM('BLUE', 'RED', 'WHITE_CELL', 'ALLIED') NOT NULL,
    `role` ENUM('EXERCISE_DIRECTOR', 'WHITE_CELL_STAFF', 'COMMANDER', 'STAFF', 'OBSERVER', 'ANALYST', 'ADMIN') NOT NULL,
    `unitScope` JSON NOT NULL,

    UNIQUE INDEX `SessionParticipant_userId_sessionId_key`(`userId`, `sessionId`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `Scenario` (
    `id` VARCHAR(191) NOT NULL,
    `name` VARCHAR(191) NOT NULL,
    `version` VARCHAR(191) NOT NULL,
    `packageBlob` LONGBLOB NOT NULL,
    `checksum` VARCHAR(191) NOT NULL,
    `createdBy` VARCHAR(191) NOT NULL,
    `createdAt` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `Order` (
    `id` VARCHAR(191) NOT NULL,
    `sessionId` VARCHAR(191) NOT NULL,
    `issuerId` VARCHAR(191) NOT NULL,
    `unitId` VARCHAR(191) NOT NULL,
    `orderType` VARCHAR(191) NOT NULL,
    `payload` JSON NOT NULL,
    `status` ENUM('PENDING', 'VALIDATED', 'EXECUTING', 'COMPLETED', 'REJECTED', 'CANCELLED') NOT NULL DEFAULT 'PENDING',
    `precheck` JSON NULL,
    `issuedAtTick` INTEGER NOT NULL,
    `resolvedAtTick` INTEGER NULL,

    INDEX `Order_sessionId_status_idx`(`sessionId`, `status`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `IntelContact` (
    `id` VARCHAR(191) NOT NULL,
    `sessionId` VARCHAR(191) NOT NULL,
    `faction` ENUM('BLUE', 'RED', 'WHITE_CELL', 'ALLIED') NOT NULL,
    `targetUnitId` VARCHAR(191) NOT NULL,
    `fidelity` ENUM('DETECTED', 'CLASSIFIED', 'IDENTIFIED') NOT NULL,
    `lastSeenTick` INTEGER NOT NULL,
    `lastSeenLat` DOUBLE NOT NULL,
    `lastSeenLng` DOUBLE NOT NULL,
    `errorRadiusM` DOUBLE NOT NULL,

    INDEX `IntelContact_sessionId_faction_idx`(`sessionId`, `faction`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `SimCheckpoint` (
    `id` VARCHAR(191) NOT NULL,
    `sessionId` VARCHAR(191) NOT NULL,
    `tick` INTEGER NOT NULL,
    `stateBlob` LONGBLOB NOT NULL,
    `stateHash` VARCHAR(191) NOT NULL,
    `createdAt` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    UNIQUE INDEX `SimCheckpoint_sessionId_tick_key`(`sessionId`, `tick`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `AIInvocationLog` (
    `id` VARCHAR(191) NOT NULL,
    `sessionId` VARCHAR(191) NULL,
    `role` VARCHAR(191) NOT NULL,
    `adapter` VARCHAR(191) NOT NULL,
    `promptHash` VARCHAR(191) NOT NULL,
    `request` JSON NOT NULL,
    `response` JSON NOT NULL,
    `latencyMs` INTEGER NOT NULL,
    `tokensIn` INTEGER NOT NULL,
    `tokensOut` INTEGER NOT NULL,
    `guardrailResult` JSON NOT NULL,
    `createdAt` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `AIInvocationLog_sessionId_role_idx`(`sessionId`, `role`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `AARReport` (
    `id` VARCHAR(191) NOT NULL,
    `sessionId` VARCHAR(191) NOT NULL,
    `narrative` JSON NOT NULL,
    `metrics` JSON NOT NULL,
    `generatedAt` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    UNIQUE INDEX `AARReport_sessionId_key`(`sessionId`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- CreateTable
CREATE TABLE `PluginRegistry` (
    `id` VARCHAR(191) NOT NULL,
    `name` VARCHAR(191) NOT NULL,
    `kind` VARCHAR(191) NOT NULL,
    `endpoint` VARCHAR(191) NOT NULL,
    `contractVer` VARCHAR(191) NOT NULL,
    `healthState` VARCHAR(191) NOT NULL,
    `config` JSON NOT NULL,
    `enabled` BOOLEAN NOT NULL DEFAULT true,

    UNIQUE INDEX `PluginRegistry_name_key`(`name`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- AddForeignKey
ALTER TABLE `TacticalUnit` ADD CONSTRAINT `TacticalUnit_sessionId_fkey` FOREIGN KEY (`sessionId`) REFERENCES `WargameSession`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `TacticalUnit` ADD CONSTRAINT `TacticalUnit_parentId_fkey` FOREIGN KEY (`parentId`) REFERENCES `TacticalUnit`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `EquipmentInstance` ADD CONSTRAINT `EquipmentInstance_templateId_fkey` FOREIGN KEY (`templateId`) REFERENCES `EquipmentTemplate`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `EquipmentInstance` ADD CONSTRAINT `EquipmentInstance_ownerId_fkey` FOREIGN KEY (`ownerId`) REFERENCES `TacticalUnit`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `TacticalEventLog` ADD CONSTRAINT `TacticalEventLog_sessionId_fkey` FOREIGN KEY (`sessionId`) REFERENCES `WargameSession`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `TacticalEventLog` ADD CONSTRAINT `TacticalEventLog_initiatorId_fkey` FOREIGN KEY (`initiatorId`) REFERENCES `TacticalUnit`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `TacticalEventLog` ADD CONSTRAINT `TacticalEventLog_targetId_fkey` FOREIGN KEY (`targetId`) REFERENCES `TacticalUnit`(`id`) ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE `SessionParticipant` ADD CONSTRAINT `SessionParticipant_userId_fkey` FOREIGN KEY (`userId`) REFERENCES `User`(`id`) ON DELETE RESTRICT ON UPDATE CASCADE;
