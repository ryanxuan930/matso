-- O6.7（ADR 006 / SPEC §12.1）：faction 由 enum → String（想定定義字串 id，N 方對抗）。
-- MySQL enum 為內聯欄型別，直接 MODIFY 為 VARCHAR 即可（既有 BLUE/RED/WHITE_CELL/ALLIED 值保留）。
ALTER TABLE `TacticalUnit` MODIFY `faction` VARCHAR(191) NOT NULL;
ALTER TABLE `SessionParticipant` MODIFY `faction` VARCHAR(191) NOT NULL;
ALTER TABLE `IntelContact` MODIFY `faction` VARCHAR(191) NOT NULL;
