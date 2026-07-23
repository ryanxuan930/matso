-- CreateTable
CREATE TABLE `MapFeature` (
    `id` VARCHAR(191) NOT NULL,
    `sessionId` VARCHAR(191) NOT NULL,
    `kind` VARCHAR(191) NOT NULL,
    `geometryType` VARCHAR(191) NOT NULL,
    `geometry` JSON NOT NULL,
    `ownerFaction` VARCHAR(191) NOT NULL,
    `label` VARCHAR(191) NULL,
    `influenceRadiusM` DOUBLE NULL,
    `weaponTemplateId` VARCHAR(191) NULL,
    `attributes` JSON NOT NULL,
    `createdAt` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- AddForeignKey
ALTER TABLE `MapFeature` ADD CONSTRAINT `MapFeature_sessionId_fkey` FOREIGN KEY (`sessionId`) REFERENCES `WargameSession`(`id`) ON DELETE CASCADE ON UPDATE CASCADE;
