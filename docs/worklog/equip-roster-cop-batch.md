---
task: "#58 裝備/名冊/COP 批次（武器庫刪除 + 裝備管理 + 依陣營分組 + unit_scope）"
status: DONE
started: 2026-07-24T11:00+08:00
updated: 2026-07-24T11:50+08:00
agent: Opus 4.8
---

# #58 裝備管理 + 名冊 unit_scope + COP 分組（使用者批次要求）

## 目標摘要（使用者一次列了 5 項）
1. 名冊 unit_scope UI（限帳號只指揮特定單位子集）。
2. 武器庫可增刪武器（原僅增/改，缺刪）。
3. 白軍統裁能編各單位配發裝備（原散在白軍控制台/資訊卡，要 COP 一級入口）。
4. 獨立「裝備管理」功能：COP 按鈕，白軍編任一單位、或開放某陣營自編。
5. 單位/下令小工具依陣營分組、可收合/展開。

## 交付（5 提交）
- **A（284595b）COP 單位小工具依陣營分組**：每陣營一組標頭（色點+名稱+數量+▸/▾）可收合；
  `unitsByFaction` 分組 + `collapsedFactions`。
- **B（85e2499）武器庫刪除**：DELETE `/equipment-templates/{tid}`（限統裁/管理；使用中→422
  EQUIPMENT_TEMPLATE_IN_USE，避免孤兒編裝）+ armory 🗑 鈕 + 二次確認。+2 測試。
- **C（ac014e5）COP 裝備管理面板**：頭列「📦 裝備管理」→ 面板（白軍勾各軍自編權限=複用
  orbat-permissions；依陣營列可編單位→UnitOrbatEditor 增刪/彈藥/數量）。**純前端**——後端
  （orbat-permissions + equipment CRUD + `_require_edit` 白軍全開）早已具備，只是缺 COP 入口。
- **D（本卡）名冊 unit_scope**：契約 SessionParticipantView/AssignParticipantRequest +unit_scope、
  ParticipantRoster +units；participants.py 讀寫+驗證（scope 單位須本局該陣營）；
  **order validator 強制**（非空 scope + 單位不在內→OrderPermissionError）；lobby 名冊每列
  🎯 展開該陣營單位勾選（不勾＝整個陣營）。unit_scope 模型型別 dict→Any（JSON 陣列）+ 統一初值 []。

## 測試證據
- 新增：equipment +2（刪除 admin/gated、使用中 422）、participants +3（roster units、scope roundtrip、
  scope 拒他陣營）、order_validator +2（scope 擋/放）。
- ruff/mypy 193/schema-sync（16 表 140 欄，D 無 DB 變更）綠、openapi 綠、**golden 6 綠**、前端 lint/typecheck 綠。
- 容器重建含新端點（DELETE template、participants unit_scope）。

## 完成 / 後續
- **完成**：五項全交付。裝備管理成 COP 一級面板；名冊可限帳號指揮單位子集且下令層強制。
- **後續**：unit_scope 也可套用到 ENGAGE 目標選擇 UI 過濾；裝備管理面板顯示「已配發總覽」。
