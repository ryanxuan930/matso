-- AlterTable
-- 補齊 2525C 有、我們缺的四個編制層級：ARMY_GROUP / ARMY / REGIMENT / SECTION。
-- 缺了它們的症狀是 2525C 的階層字母 L/K/G/C 永遠用不到，且 CORPS 之上直接跳 THEATER。
--
-- **順序即大小**：MySQL ENUM 的成員順序在此無語義（後端用 Python enum 的宣告順序），
-- 但仍照大小排以免下一個人看了兩邊不一致而困惑。既有列的值是字串，不受影響。
ALTER TABLE `TacticalUnit`
  MODIFY COLUMN `unitLevel` ENUM(
    'THEATER','ARMY_GROUP','ARMY','CORPS','DIVISION','BRIGADE','REGIMENT',
    'BATTALION','COMPANY','PLATOON','SECTION','SQUAD','FIRETEAM','INDIVIDUAL'
  ) NOT NULL;
