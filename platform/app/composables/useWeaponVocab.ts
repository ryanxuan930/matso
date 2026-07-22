// 武器/裝備詞彙的繁中標籤（#4）——類別、裝甲級別、命中率插值法。
// 與 contracts/weaponeering.schema.json 的 $defs 對齊（armor_class 詞彙、ph_interp、category）。
// 供武器庫（armory.vue）與地圖編輯器（#11）武器據點屬性面板共用。

/** 裝備類別 → 繁中。對齊 EquipmentTemplate.category。 */
export const CATEGORY_LABELS: Record<string, string> = {
  KINETIC: '火力（動能武器）',
  SENSOR: '感測器',
  COMMS: '通信',
  LOGISTICS: '後勤',
  DRONE: '無人機',
}
export const CATEGORIES = Object.keys(CATEGORY_LABELS)
export function categoryLabel(c?: string): string {
  return (c && CATEGORY_LABELS[c]) || c || '—'
}

/**
 * 裝甲級別標準詞彙（對齊 weaponeering.schema.json $defs.armor_class）。
 * 建議詞彙、非封閉——UI 提供此清單為下拉選項，另允許自訂鍵以保留可擴充性。
 */
export const ARMOR_CLASS_LABELS: Record<string, string> = {
  INFANTRY: '步兵／人員',
  LIGHT_VEHICLE: '輕型載具',
  ARMOR: '裝甲／戰車',
  FORTIFICATION: '工事／掩體',
  STRUCTURE: '建物',
  AIRCRAFT: '固定翼航空器',
  ROTARY_WING: '旋翼機',
  NAVAL_SURFACE: '水面艦艇',
  UAS: '無人機／UAS',
}
export const ARMOR_CLASSES = Object.keys(ARMOR_CLASS_LABELS)
export function armorClassLabel(ac?: string): string {
  return (ac && ARMOR_CLASS_LABELS[ac]) || ac || '—'
}

/** 命中率對射程的插值法 → 繁中（對齊 ph_interp）。 */
export const PH_INTERP_LABELS: Record<string, string> = {
  linear: '線性插值（控制點間直線）',
  polynomial: '多項式插值（拉格朗日曲線，需 ≥3 點）',
}
export const PH_INTERP_MODES = Object.keys(PH_INTERP_LABELS)
