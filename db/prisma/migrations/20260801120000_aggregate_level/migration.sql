-- AlterTable
-- 聚合裁決門檻。想定 schema 早就有 `aggregate_adjudication_level`、loader 也讀得進來，
-- 但**從來沒有被持久化**，於是 `should_aggregate()` 永遠吃預設的 BATTALION
-- ——想定寫了 COMPANY 或 BRIGADE 完全沒有作用（roundtrip 測試仍綠，因為 loader→dump 對得上）。
--
-- NULL＝沿用既有預設 BATTALION，既有局零影響。
ALTER TABLE `WargameSession`
  ADD COLUMN `aggregateAdjudicationLevel` ENUM(
    'THEATER','ARMY_GROUP','ARMY','CORPS','DIVISION','BRIGADE','REGIMENT',
    'BATTALION','COMPANY','PLATOON','SECTION','SQUAD','FIRETEAM','INDIVIDUAL'
  ) NULL;
