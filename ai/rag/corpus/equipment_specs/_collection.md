# equipment_specs — 裝備參數

裝備規格：射程、感測範圍、機動速度、耗油/補給率、防護等級等。供全角色引用查核（G5），
並與種子武器模板（`weaponeering.schema.json`）對應。

放：單項裝備一節，具體數字（例：「射程 X km、感測 Y km、公路速度 Z km/h」）。
不放：戰術準則（→ `doctrine_*`）。數字須可被逐字引用——這是 G5 相似度核對的依據。

每份文件需 front-matter（`collection: equipment_specs`，`doctrine_side: NA`），段落給穩定錨點。
格式規範見上層 [`../README.md`](../README.md)。
