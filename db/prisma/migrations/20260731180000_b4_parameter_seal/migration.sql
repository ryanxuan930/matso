-- WP-B4 參數簽證。一場演習最多一份。
-- snapshotBlob 用 LONGBLOB（存 zstd 壓縮的 canonical JSON）：EquipmentTemplate 全表
-- 未壓縮可能不小，而 HOW_TO.md:357 已記過 Bytes 欄的 16MB 限制（SimCheckpoint 踩過）。

CREATE TABLE `ParameterSeal` (
    `id` VARCHAR(191) NOT NULL,
    `exerciseId` VARCHAR(191) NOT NULL,
    `sealedAt` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    `sealedBy` VARCHAR(191) NOT NULL,
    `contentHash` VARCHAR(191) NOT NULL,
    `snapshotBlob` LONGBLOB NOT NULL,

    UNIQUE INDEX `ParameterSeal_exerciseId_key`(`exerciseId`),
    PRIMARY KEY (`id`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
