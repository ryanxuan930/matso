-- #6：per-faction「自行編輯本軍編裝」權限清單（White Cell 設定）。null = 僅白軍可編。
ALTER TABLE `WargameSession` ADD COLUMN `orbatEditFactions` JSON NULL;
