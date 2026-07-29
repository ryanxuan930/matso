-- AlterTable
ALTER TABLE `SessionParticipant` ADD COLUMN `seatRole` ENUM('COMMANDER', 'S2_INTEL', 'S3_OPS', 'FSO_FIRES', 'S4_LOG', 'OBSERVER') NULL;
