-- WP-B6 想定機動覆寫持久化：WargameSession 新增 mobilityOverrides（局部覆寫 JSON 物件）。
-- 可為 NULL：既有推演局維持 NULL ＝ 用出貨預設矩陣（過去語義，零資料遷移）。
-- 存**局部**覆寫而非展開後的完整矩陣：想定作者只改幾格，存全量會讓日後調整出貨預設時
-- 既有局全部被凍結在舊值（而那些值並非想定作者的意圖）。
-- ⚠ 覆寫不得改變可通行性（-1 進出）——A* 在 terrain 容器讀它自己那份預設，看不到本欄；
-- 詳見 core/app/scenario/loader.py::_validate_mobility_passability。
-- AlterTable
ALTER TABLE `WargameSession` ADD COLUMN `mobilityOverrides` JSON NULL;
