# terrain_analysis — 地形分析原則

地形對機動/火力/掩蔽的影響準則（供 STRATEGIC_PLANNER 與 OPFOR_COMMANDER 引用）。
注意：**具體某局的地形事實由 Terrain 引擎裁決**（LOS/viewshed/A*），此處只放「原則」語料，
不放特定 hex 的可視/可達判定——AI 沒有繞過物理引擎的特權。

放：通道/隘口識別原則、掩蔽與遮蔽運用、高地價值判準、渡河點考量。
不放：某局特定座標的裁決結果（那是引擎的事）。

每份文件需 front-matter（`collection: terrain_analysis`），段落給穩定錨點。
格式規範見上層 [`../README.md`](../README.md)。
