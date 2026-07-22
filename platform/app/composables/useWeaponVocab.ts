// 武器/裝備詞彙的繁中標籤（#4）——類別、裝甲級別、命中率插值法。
// 與 contracts/weaponeering.schema.json 的 $defs 對齊（armor_class 詞彙、ph_interp、category）。
// 供武器庫（armory.vue）與地圖編輯器（#11）武器據點屬性面板共用。

/** 裝備類別 → 繁中。對齊 EquipmentTemplate.category。 */
export const CATEGORY_LABELS: Record<string, string> = {
  KINETIC: '火力（直射動能）',
  MISSILE: '飛彈（導引）',
  ARTILLERY: '火砲（間瞄）',
  VEHICLE: '載具',
  SENSOR: '感測器',
  COMMS: '通信',
  LOGISTICS: '後勤',
  DRONE: '無人機',
}

/** 飛彈類型 → 繁中（對齊 missile_kind）。 */
export const MISSILE_KIND_LABELS: Record<string, string> = {
  ATGM: '反戰車飛彈',
  SAM: '地對空飛彈',
  MANPADS: '肩射防空飛彈',
  CRUISE: '巡弋飛彈',
  BALLISTIC: '彈道飛彈',
  LOITERING: '遊蕩彈藥',
  ANTI_SHIP: '反艦飛彈',
  AAM: '空對空飛彈',
}
export const MISSILE_KINDS = Object.keys(MISSILE_KIND_LABELS)

/** 導引方式 → 繁中（對齊 guidance）。 */
export const GUIDANCE_LABELS: Record<string, string> = {
  WIRE: '導線',
  SACLOS: '半自動瞄準線（SACLOS）',
  LASER_BEAM_RIDING: '雷射駕束',
  LASER_SAL: '雷射半主動',
  IR: '紅外',
  RADAR_SARH: '雷達半主動',
  RADAR_ARH: '雷達主動',
  GPS_INS: 'GPS＋慣性',
  COMMAND: '指令導引',
  FIRE_AND_FORGET: '射後不理',
}
export const GUIDANCE_MODES = Object.keys(GUIDANCE_LABELS)

/** 尋標器 → 繁中（對齊 seeker）。 */
export const SEEKER_LABELS: Record<string, string> = {
  NONE: '無',
  IR: '紅外',
  RADAR: '雷達',
  LASER: '雷射',
  OPTICAL: '光學',
  MMW: '毫米波',
  DUAL_MODE: '雙模',
}
export const SEEKER_TYPES = Object.keys(SEEKER_LABELS)

/** 戰鬥部 → 繁中（對齊 warhead）。 */
export const WARHEAD_LABELS: Record<string, string> = {
  HEAT: '成形裝藥（HEAT）',
  TANDEM_HEAT: '串聯成形裝藥',
  HE_FRAG: '高爆破片',
  PENETRATOR: '穿甲',
  THERMOBARIC: '溫壓',
  BLAST_FRAG: '爆震破片',
}
export const WARHEAD_TYPES = Object.keys(WARHEAD_LABELS)

/** 感測器類型 → 繁中（對齊 sensor_kind）。 */
export const SENSOR_KIND_LABELS: Record<string, string> = {
  OPTICAL: '光學',
  IR: '紅外／熱像',
  RADAR: '雷達',
  ACOUSTIC: '聲學',
  SIGINT: '電子偵蒐（SIGINT）',
}
export const SENSOR_KINDS = Object.keys(SENSOR_KIND_LABELS)

/** 通信頻段 → 繁中（對齊 comms band）。 */
export const COMMS_BAND_LABELS: Record<string, string> = {
  LORA: 'LoRa',
  MESHTASTIC: 'Meshtastic',
  VHF: 'VHF',
  UHF: 'UHF',
  SATCOM: '衛星通信（SATCOM）',
}
export const COMMS_BANDS = Object.keys(COMMS_BAND_LABELS)

/** 無人機類型 → 繁中（對齊 drone_kind）。 */
export const DRONE_KIND_LABELS: Record<string, string> = {
  RECON: '偵察',
  STRIKE: '打擊',
  EW: '電子戰',
  LOITERING_MUNITION: '遊蕩彈藥',
  VTOL_CARGO: '垂直起降運補',
  TARGET_ACQ: '目標獲取',
}
export const DRONE_KINDS = Object.keys(DRONE_KIND_LABELS)

/** 無人機感測酬載 → 繁中（對齊 sensor_payload）。 */
export const SENSOR_PAYLOAD_LABELS: Record<string, string> = {
  EO_IR: '光電／紅外（EO/IR）',
  SAR: '合成孔徑雷達（SAR）',
  SIGINT: '電子偵蒐',
  NONE: '無',
}
export const SENSOR_PAYLOADS = Object.keys(SENSOR_PAYLOAD_LABELS)

/** 後勤補給類別 → 繁中（capacity 鍵）。 */
export const SUPPLY_CLASS_LABELS: Record<string, string> = {
  AMMO: '彈藥',
  FUEL: '油料',
  WATER_FOOD: '水／糧',
  BATTERY: '電池',
}
export const SUPPLY_CLASSES = Object.keys(SUPPLY_CLASS_LABELS)

/** 動能武器細分（現代軍事分類）→ 繁中（對齊 kinetic_kind）。 */
export const KINETIC_KIND_LABELS: Record<string, string> = {
  SMALL_ARMS: '輕兵器（步/機槍）',
  AUTOCANNON: '機砲',
  ATGM: '反戰車飛彈',
  TANK_MAIN_GUN: '戰車主砲',
  GRENADE: '榴彈／擲彈',
  GENERIC: '通用',
}
export const KINETIC_KINDS = Object.keys(KINETIC_KIND_LABELS)

/** 火砲細分 → 繁中（對齊 artillery_kind）。 */
export const ARTILLERY_KIND_LABELS: Record<string, string> = {
  MORTAR: '迫擊砲',
  TOWED_GUN: '牽引火砲',
  SP_GUN: '自走砲',
  MLRS: '多管火箭',
}
export const ARTILLERY_KINDS = Object.keys(ARTILLERY_KIND_LABELS)

/** 機動類型 → 繁中（對齊 mobility_class）。 */
export const MOBILITY_CLASS_LABELS: Record<string, string> = {
  WHEELED: '輪型',
  TRACKED: '履帶',
  TOWED: '牽引（須載運）',
  MAN_PORTABLE: '人力攜行',
  STATIC: '固定（不可自走）',
  AIR: '空中',
}
export const MOBILITY_CLASSES = Object.keys(MOBILITY_CLASS_LABELS)

/** 穿甲機制 → 繁中（對齊 penetration_type）。 */
export const PENETRATION_TYPE_LABELS: Record<string, string> = {
  KE: '動能穿甲（隨距衰減）',
  HEAT: '成形裝藥（平坦）',
  HE_FRAG: '高爆破片',
  NONE: '無',
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
