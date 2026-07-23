<script setup lang="ts">
// 武器庫（裝備範本編輯）——編輯 EquipmentTemplate.base_stats（屬性/功能）。KINETIC/ARTILLERY/
// VEHICLE 走結構化表單（比照兵推）；SENSOR/COMMS/LOGISTICS/DRONE 走預填 JSON scaffold。
// 任何類別皆可「一鍵切換 表單/JSON」（保留擴充屬性：儲存時結構化欄位覆蓋於原 baseStats 之上）。
import {
  createEquipmentTemplate,
  fetchEquipmentTemplates,
  updateEquipmentTemplate,
  type EquipmentTemplate,
} from '~/composables/useEquipment'
import {
  ARMOR_CLASSES,
  ARMOR_CLASS_LABELS,
  ARTILLERY_KIND_LABELS,
  ARTILLERY_KINDS,
  CATEGORIES,
  categoryLabel,
  COMMS_BAND_LABELS,
  COMMS_BANDS,
  DRONE_KIND_LABELS,
  DRONE_KINDS,
  GUIDANCE_LABELS,
  GUIDANCE_MODES,
  KINETIC_KIND_LABELS,
  KINETIC_KINDS,
  MISSILE_KIND_LABELS,
  MISSILE_KINDS,
  MOBILITY_CLASS_LABELS,
  MOBILITY_CLASSES,
  PH_INTERP_LABELS,
  PH_INTERP_MODES,
  SEEKER_LABELS,
  SEEKER_TYPES,
  SENSOR_KIND_LABELS,
  SENSOR_KINDS,
  SENSOR_PAYLOAD_LABELS,
  SENSOR_PAYLOADS,
  SUPPLY_CLASS_LABELS,
  WARHEAD_LABELS,
  WARHEAD_TYPES,
} from '~/composables/useWeaponVocab'

const toasts = useToasts()

const templates = ref<EquipmentTemplate[]>([])
const selectedId = ref<string | null>(null) // null = 新增中
const name = ref('')
const category = ref('KINETIC')
const busy = ref(false)
const editMode = ref<'form' | 'json'>('form') // 一鍵切換 表單/JSON
const originalBaseStats = ref<Record<string, unknown>>({}) // 儲存時保留未涵蓋的擴充鍵

// KINETIC / ARTILLERY 共用火力欄位
const maxRange = ref(600)
const minRange = ref(0)
const indirectFire = ref(false)
const ratePerTick = ref(1)
const ammoTypes = ref('') // 逗號分隔
const kineticKind = ref('GENERIC')
const phInterp = ref('linear') // 命中率插值法（#4）：linear | polynomial
const phBands = ref<{ range: number; ph: number }[]>([{ range: 300, ph: 0.5 }])
const dmgRows = ref<{ ac: string; dmg: number }[]>([{ ac: 'INFANTRY', dmg: 30 }])
const pkRows = ref<{ ac: string; pk: number }[]>([{ ac: 'INFANTRY', pk: 0.5 }]) // 每發擊殺率（真實化）
// ARTILLERY 專屬
const artilleryKind = ref('MORTAR')
const dispersionCep = ref(90)
const lethalRadius = ref(35)
const roundsPerMission = ref(6)
// VEHICLE 專屬
const crew = ref(3)
const passengerCap = ref(0)
const vehArmorClass = ref('ARMOR')
const armorFront = ref(200)
const armorSide = ref(80)
const armorRear = ref(40)
const armorTop = ref(20)
// 共用機動性（ARTILLERY/VEHICLE/LOGISTICS）
const canSelfMove = ref(true)
const mobilityClass = ref('TRACKED')
const roadSpeed = ref(60)
const ccSpeed = ref(35)
// MISSILE 專屬（飛彈諸元）
const missileKind = ref('ATGM')
const guidance = ref('SACLOS')
const seeker = ref('NONE')
const warhead = ref('HEAT')
const flightSpeed = ref(280)
const topAttack = ref(false)
const minEngageRange = ref(65)
const cmResistance = ref(0.5)
const missileManeuverable = ref(true) // 可變軌（巡弋僅判射程）；false＝彈道飛彈走拋物線
const apexRatio = ref(0.25) // 拋物線頂高比（彈道飛彈的地形/障礙淨空判定用）
// SENSOR 專屬
const sensorKind = ref('OPTICAL')
const sensorMaxRange = ref(5000)
const detectCurve = ref<{ range: number; p: number }[]>([{ range: 2000, p: 0.9 }])
const identifyRange = ref(2500)
const fovDeg = ref(60)
const scanPeriod = ref(1)
const sensorPassive = ref(true)
const minRcs = ref(1)
// COMMS 專屬
const commsBand = ref('VHF')
const txPower = ref(37)
const rxSens = ref(-100)
const antGain = ref(2)
const meshCapable = ref(false)
const freqMhz = ref(50)
const dataRate = ref(9.6)
const encrypted = ref(false)
const leoSatcom = ref(false)
// LOGISTICS 專屬
const capRows = ref<{ cls: string; amt: number }[]>([
  { cls: 'AMMO', amt: 100 },
  { cls: 'FUEL', amt: 200 },
])
const troopCap = ref(0)
const vehicleSlots = ref(0)
const canTow = ref(false)
const loadUnloadTicks = ref(1)
const resupplyRate = ref(20)
const logCrew = ref(2)
// DRONE 專屬
const droneKind = ref('RECON')
const enduranceTicks = ref(120)
const cruiseSpeed = ref(25)
const serviceCeiling = ref(3000)
const dataLinkRange = ref(15000)
const payloadKg = ref(3)
const sensorPayload = ref('EO_IR')
const isExpendable = ref(false)
const maxWind = ref(12)
const minVis = ref(1000)
// JSON 編輯（任何類別皆可切換檢視/編修原始 JSON）
const jsonText = ref('{}')

// 所有類別皆有結構化表單（可一鍵切 JSON）。
const FORM_CATEGORIES = ['KINETIC', 'MISSILE', 'ARTILLERY', 'VEHICLE', 'SENSOR', 'COMMS', 'LOGISTICS', 'DRONE']
const hasForm = computed(() => FORM_CATEGORIES.includes(category.value))
// 火力型（走 kinetic 火力欄位 + 傷害/pk）：直射動能 / 火砲 / 飛彈皆 allOf kinetic。
const isKineticLike = computed(
  () => category.value === 'KINETIC' || category.value === 'ARTILLERY' || category.value === 'MISSILE',
)

async function load() {
  templates.value = await fetchEquipmentTemplates().catch(() => [])
}
onMounted(load)

function resetForm() {
  selectedId.value = null
  name.value = ''
  category.value = 'KINETIC'
  originalBaseStats.value = {}
  editMode.value = 'form'
  maxRange.value = 600
  minRange.value = 0
  indirectFire.value = false
  ratePerTick.value = 1
  ammoTypes.value = 'AMMO_GENERIC'
  kineticKind.value = 'GENERIC'
  phInterp.value = 'linear'
  phBands.value = [{ range: 300, ph: 0.5 }]
  dmgRows.value = [{ ac: 'INFANTRY', dmg: 30 }]
  pkRows.value = [{ ac: 'INFANTRY', pk: 0.5 }]
  artilleryKind.value = 'MORTAR'
  dispersionCep.value = 90
  lethalRadius.value = 35
  roundsPerMission.value = 6
  crew.value = 3
  passengerCap.value = 0
  vehArmorClass.value = 'ARMOR'
  armorFront.value = 200
  armorSide.value = 80
  armorRear.value = 40
  armorTop.value = 20
  canSelfMove.value = true
  mobilityClass.value = 'TRACKED'
  roadSpeed.value = 60
  ccSpeed.value = 35
  missileKind.value = 'ATGM'
  guidance.value = 'SACLOS'
  seeker.value = 'NONE'
  warhead.value = 'HEAT'
  flightSpeed.value = 280
  topAttack.value = false
  minEngageRange.value = 65
  cmResistance.value = 0.5
  missileManeuverable.value = true
  apexRatio.value = 0.25
  sensorKind.value = 'OPTICAL'
  sensorMaxRange.value = 5000
  detectCurve.value = [{ range: 2000, p: 0.9 }]
  identifyRange.value = 2500
  fovDeg.value = 60
  scanPeriod.value = 1
  sensorPassive.value = true
  minRcs.value = 1
  commsBand.value = 'VHF'
  txPower.value = 37
  rxSens.value = -100
  antGain.value = 2
  meshCapable.value = false
  freqMhz.value = 50
  dataRate.value = 9.6
  encrypted.value = false
  leoSatcom.value = false
  capRows.value = [
    { cls: 'AMMO', amt: 100 },
    { cls: 'FUEL', amt: 200 },
  ]
  troopCap.value = 0
  vehicleSlots.value = 0
  canTow.value = false
  loadUnloadTicks.value = 1
  resupplyRate.value = 20
  logCrew.value = 2
  droneKind.value = 'RECON'
  enduranceTicks.value = 120
  cruiseSpeed.value = 25
  serviceCeiling.value = 3000
  dataLinkRange.value = 15000
  payloadKg.value = 3
  sensorPayload.value = 'EO_IR'
  isExpendable.value = false
  maxWind.value = 12
  minVis.value = 1000
  jsonText.value = '{}'
}

// 使用者改類別（新範本）：清擴充鍵，切回結構化表單（各類別皆有表單，欄位取自 ref 預設）。
function onCategoryChange() {
  originalBaseStats.value = {}
  editMode.value = 'form' // 各類別皆有結構化表單；欄位取自各 ref 的預設值
}

function populateForm(bs: Record<string, unknown>): void {
  if (isKineticLike.value) {
    maxRange.value = Number(bs.max_range_m ?? 600)
    minRange.value = Number(bs.min_range_m ?? 0)
    indirectFire.value = Boolean(bs.indirect_fire ?? category.value === 'ARTILLERY')
    ratePerTick.value = Number(bs.rate_per_tick ?? 1)
    kineticKind.value = String(bs.kinetic_kind ?? 'GENERIC')
    phInterp.value = (bs.ph_interp as string) === 'polynomial' ? 'polynomial' : 'linear'
    ammoTypes.value = ((bs.ammo_types as string[]) ?? []).join(', ')
    phBands.value = ((bs.ph_by_range_band as [number, number][]) ?? []).map(([range, ph]) => ({
      range,
      ph,
    }))
    dmgRows.value = Object.entries((bs.damage_by_armor_class as Record<string, number>) ?? {}).map(
      ([ac, dmg]) => ({ ac, dmg }),
    )
    pkRows.value = Object.entries((bs.pk_by_armor_class as Record<string, number>) ?? {}).map(
      ([ac, pk]) => ({ ac, pk }),
    )
    if (category.value === 'ARTILLERY') {
      artilleryKind.value = String(bs.artillery_kind ?? 'MORTAR')
      dispersionCep.value = Number(bs.dispersion_cep_m ?? 90)
      lethalRadius.value = Number(bs.lethal_radius_m ?? 35)
      roundsPerMission.value = Number(bs.rounds_per_mission ?? 6)
      readMobility((bs.mobility as Record<string, unknown>) ?? {})
    }
    if (category.value === 'MISSILE') {
      missileKind.value = String(bs.missile_kind ?? 'ATGM')
      guidance.value = String(bs.guidance ?? 'SACLOS')
      seeker.value = String(bs.seeker ?? 'NONE')
      warhead.value = String(bs.warhead ?? 'HEAT')
      flightSpeed.value = Number(bs.flight_speed_ms ?? 280)
      topAttack.value = Boolean(bs.top_attack ?? false)
      minEngageRange.value = Number(bs.min_engage_range_m ?? 0)
      cmResistance.value = Number(bs.countermeasure_resistance ?? 0.5)
      missileManeuverable.value = bs.maneuverable !== false
      apexRatio.value = Number(bs.apex_ratio ?? 0.25)
    }
  } else if (category.value === 'VEHICLE') {
    crew.value = Number(bs.crew ?? 3)
    passengerCap.value = Number(bs.passenger_capacity ?? 0)
    vehArmorClass.value = String(bs.armor_class ?? 'ARMOR')
    const a = (bs.armor_by_aspect_mm as Record<string, number>) ?? {}
    armorFront.value = Number(a.front ?? 200)
    armorSide.value = Number(a.side ?? 80)
    armorRear.value = Number(a.rear ?? 40)
    armorTop.value = Number(a.top ?? 20)
    readMobility((bs.mobility as Record<string, unknown>) ?? {})
  } else if (category.value === 'SENSOR') {
    sensorKind.value = String(bs.sensor_kind ?? 'OPTICAL')
    sensorMaxRange.value = Number(bs.max_range_m ?? 5000)
    detectCurve.value = ((bs.detect_curve as [number, number][]) ?? []).map(([range, p]) => ({
      range,
      p,
    }))
    if (!detectCurve.value.length) detectCurve.value = [{ range: 2000, p: 0.9 }]
    identifyRange.value = Number(bs.identify_range_m ?? 2500)
    fovDeg.value = Number(bs.fov_deg ?? 60)
    scanPeriod.value = Number(bs.scan_period_ticks ?? 1)
    sensorPassive.value = bs.passive !== false
    minRcs.value = Number(bs.min_target_rcs_m2 ?? 1)
  } else if (category.value === 'COMMS') {
    commsBand.value = String(bs.band ?? 'VHF')
    txPower.value = Number(bs.tx_power_dbm ?? 37)
    rxSens.value = Number(bs.rx_sensitivity_dbm ?? -100)
    antGain.value = Number(bs.antenna_gain_dbi ?? 2)
    meshCapable.value = Boolean(bs.mesh_capable ?? false)
    freqMhz.value = Number(bs.freq_mhz ?? 50)
    dataRate.value = Number(bs.data_rate_kbps ?? 9.6)
    encrypted.value = Boolean(bs.encrypted ?? false)
    leoSatcom.value = Boolean(bs.leo_satcom ?? false)
  } else if (category.value === 'LOGISTICS') {
    capRows.value = Object.entries((bs.capacity as Record<string, number>) ?? {}).map(
      ([cls, amt]) => ({ cls, amt }),
    )
    if (!capRows.value.length) capRows.value = [{ cls: 'AMMO', amt: 100 }]
    const tr = (bs.transport as Record<string, unknown>) ?? {}
    troopCap.value = Number(tr.troop_capacity ?? 0)
    vehicleSlots.value = Number(tr.vehicle_slots ?? 0)
    canTow.value = Boolean(tr.can_tow ?? false)
    loadUnloadTicks.value = Number(tr.load_unload_ticks ?? 1)
    resupplyRate.value = Number(bs.resupply_rate_per_tick ?? 20)
    logCrew.value = Number(bs.crew ?? 2)
    readMobility((bs.mobility as Record<string, unknown>) ?? {})
  } else if (category.value === 'DRONE') {
    droneKind.value = String(bs.drone_kind ?? 'RECON')
    enduranceTicks.value = Number(bs.endurance_ticks ?? 120)
    cruiseSpeed.value = Number(bs.cruise_speed_ms ?? 25)
    serviceCeiling.value = Number(bs.service_ceiling_m ?? 3000)
    dataLinkRange.value = Number(bs.data_link_range_m ?? 15000)
    payloadKg.value = Number(bs.payload_kg ?? 3)
    sensorPayload.value = String(bs.sensor_payload ?? 'EO_IR')
    isExpendable.value = Boolean(bs.is_expendable ?? false)
    const wl = (bs.weather_limits as Record<string, number>) ?? {}
    maxWind.value = Number(wl.max_wind_ms ?? 12)
    minVis.value = Number(wl.min_visibility_m ?? 1000)
  }
}

function readMobility(m: Record<string, unknown>): void {
  canSelfMove.value = m.can_self_move !== false
  mobilityClass.value = String(m.mobility_class ?? 'TRACKED')
  roadSpeed.value = Number(m.max_road_speed_kmh ?? 60)
  ccSpeed.value = Number(m.max_cross_country_speed_kmh ?? 35)
}

function mobilityStats(): Record<string, unknown> {
  return {
    can_self_move: canSelfMove.value,
    mobility_class: mobilityClass.value,
    max_road_speed_kmh: roadSpeed.value,
    max_cross_country_speed_kmh: ccSpeed.value,
  }
}

function pick(t: EquipmentTemplate) {
  selectedId.value = t.id
  name.value = t.name
  category.value = t.category
  const bs = (t.base_stats ?? {}) as Record<string, unknown>
  originalBaseStats.value = bs
  if (hasForm.value) {
    populateForm(bs)
    editMode.value = 'form'
  } else {
    jsonText.value = JSON.stringify(bs, null, 2)
    editMode.value = 'json'
  }
}

// 結構化欄位 → baseStats（表單模式），覆蓋於原 baseStats 之上（保留擴充鍵）。
function formToBaseStats(): Record<string, unknown> {
  const kvNum = (rows: { ac: string; v: number }[]) =>
    Object.fromEntries(rows.filter((r) => r.ac.trim() && r.ac !== '__custom__').map((r) => [r.ac.trim(), r.v]))
  if (isKineticLike.value) {
    const base: Record<string, unknown> = {
      max_range_m: maxRange.value,
      min_range_m: minRange.value,
      indirect_fire: indirectFire.value,
      rate_per_tick: ratePerTick.value,
      kinetic_kind: kineticKind.value,
      ammo_types: ammoTypes.value.split(',').map((s) => s.trim()).filter(Boolean),
      ph_interp: phInterp.value,
      ph_by_range_band: phBands.value.map((b) => [b.range, b.ph]),
      damage_by_armor_class: kvNum(dmgRows.value.map((r) => ({ ac: r.ac, v: r.dmg }))),
      pk_by_armor_class: kvNum(pkRows.value.map((r) => ({ ac: r.ac, v: r.pk }))),
    }
    if (category.value === 'ARTILLERY') {
      base.artillery_kind = artilleryKind.value
      base.dispersion_cep_m = dispersionCep.value
      base.lethal_radius_m = lethalRadius.value
      base.rounds_per_mission = roundsPerMission.value
      base.mobility = mobilityStats()
    }
    if (category.value === 'MISSILE') {
      base.missile_kind = missileKind.value
      base.guidance = guidance.value
      base.seeker = seeker.value
      base.warhead = warhead.value
      base.flight_speed_ms = flightSpeed.value
      base.top_attack = topAttack.value
      base.min_engage_range_m = minEngageRange.value
      base.countermeasure_resistance = cmResistance.value
      base.maneuverable = missileManeuverable.value
      base.apex_ratio = apexRatio.value
    }
    return base
  }
  if (category.value === 'VEHICLE') {
    return {
      crew: crew.value,
      passenger_capacity: passengerCap.value,
      armor_class: vehArmorClass.value,
      armor_by_aspect_mm: {
        front: armorFront.value,
        side: armorSide.value,
        rear: armorRear.value,
        top: armorTop.value,
      },
      mobility: mobilityStats(),
    }
  }
  if (category.value === 'SENSOR') {
    return {
      sensor_kind: sensorKind.value,
      max_range_m: sensorMaxRange.value,
      detect_curve: detectCurve.value.map((d) => [d.range, d.p]),
      identify_range_m: identifyRange.value,
      fov_deg: fovDeg.value,
      scan_period_ticks: scanPeriod.value,
      passive: sensorPassive.value,
      min_target_rcs_m2: minRcs.value,
    }
  }
  if (category.value === 'COMMS') {
    return {
      band: commsBand.value,
      tx_power_dbm: txPower.value,
      rx_sensitivity_dbm: rxSens.value,
      antenna_gain_dbi: antGain.value,
      mesh_capable: meshCapable.value,
      freq_mhz: freqMhz.value,
      data_rate_kbps: dataRate.value,
      encrypted: encrypted.value,
      leo_satcom: leoSatcom.value,
    }
  }
  if (category.value === 'LOGISTICS') {
    return {
      capacity: Object.fromEntries(
        capRows.value.filter((r) => r.cls.trim()).map((r) => [r.cls.trim(), r.amt]),
      ),
      transport: {
        troop_capacity: troopCap.value,
        vehicle_slots: vehicleSlots.value,
        can_tow: canTow.value,
        load_unload_ticks: loadUnloadTicks.value,
      },
      resupply_rate_per_tick: resupplyRate.value,
      crew: logCrew.value,
      mobility: mobilityStats(),
    }
  }
  // DRONE
  return {
    drone_kind: droneKind.value,
    endurance_ticks: enduranceTicks.value,
    cruise_speed_ms: cruiseSpeed.value,
    service_ceiling_m: serviceCeiling.value,
    data_link_range_m: dataLinkRange.value,
    payload_kg: payloadKg.value,
    sensor_payload: sensorPayload.value,
    is_expendable: isExpendable.value,
    weather_limits: { max_wind_ms: maxWind.value, min_visibility_m: minVis.value },
  }
}

function buildBaseStats(): Record<string, unknown> {
  if (editMode.value === 'json' || !hasForm.value) {
    return JSON.parse(jsonText.value) as Record<string, unknown>
  }
  // 表單模式：結構化欄位覆蓋於原 baseStats 之上，保留未涵蓋的自訂/擴充鍵。
  return { ...originalBaseStats.value, ...formToBaseStats() }
}

// 一鍵切換 表單↔JSON，往返同步（form→json 序列化目前表單；json→form 盡力解析回填）。
function toggleMode() {
  if (editMode.value === 'form') {
    jsonText.value = JSON.stringify(buildBaseStats(), null, 2)
    editMode.value = 'json'
  } else {
    try {
      const parsed = JSON.parse(jsonText.value) as Record<string, unknown>
      originalBaseStats.value = parsed
      if (hasForm.value) populateForm(parsed)
      editMode.value = 'form'
    } catch {
      toasts.push({ severity: 'error', title: 'JSON 格式錯誤', detail: '無法切回表單', timeoutMs: 4000 })
    }
  }
}

async function save() {
  if (!name.value.trim()) {
    toasts.push({ severity: 'warn', title: '請填範本名稱', timeoutMs: 3000 })
    return
  }
  let baseStats: Record<string, unknown>
  try {
    baseStats = buildBaseStats()
  } catch {
    toasts.push({ severity: 'error', title: 'JSON 格式錯誤', detail: '請檢查屬性 JSON', timeoutMs: 0 })
    return
  }
  busy.value = true
  const body = { name: name.value.trim(), category: category.value, base_stats: baseStats }
  try {
    const saved = selectedId.value
      ? await updateEquipmentTemplate(selectedId.value, body)
      : await createEquipmentTemplate(body)
    await load()
    pick(saved)
    toasts.push({ severity: 'success', title: `已儲存：${saved.name}`, timeoutMs: 3000 })
  } catch (e) {
    const err = e as { code?: string; message?: string }
    toasts.push({
      severity: 'error',
      title: `儲存失敗${err.code ? `（${err.code}）` : ''}`,
      detail: err.message ?? '請確認你有統裁/管理權限，且屬性符合規格',
      timeoutMs: 0,
    })
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="armory">
    <header>
      <button class="back" data-testid="armory-back" @click="navigateTo('/lobby')">← 系統首頁</button>
      <h1>武器庫</h1>
    </header>

    <div class="body">
      <aside class="list">
        <button class="new" data-testid="armory-new" @click="resetForm">＋ 新增範本</button>
        <ul>
          <li
            v-for="t in templates"
            :key="t.id"
            :class="{ sel: t.id === selectedId }"
            data-testid="armory-item"
            @click="pick(t)"
          >
            <span class="t-name">{{ t.name }}</span>
            <span class="t-cat">{{ t.category }}</span>
          </li>
          <li v-if="!templates.length" class="empty">（尚無範本）</li>
        </ul>
      </aside>

      <section class="editor" data-testid="armory-editor">
        <h2>{{ selectedId ? '編輯範本' : '新增範本' }}</h2>
        <div class="row">
          <label>名稱 <input v-model="name" data-testid="armory-name"></label>
          <label>類別
            <select v-model="category" data-testid="armory-category" @change="onCategoryChange">
              <option v-for="c in CATEGORIES" :key="c" :value="c">{{ categoryLabel(c) }}</option>
            </select>
          </label>
          <button
            v-if="hasForm"
            class="mode-toggle"
            data-testid="armory-mode-toggle"
            :title="editMode === 'form' ? '切換為 JSON 編輯' : '切換回表單編輯'"
            @click="toggleMode"
          >
            {{ editMode === 'form' ? '⤳ JSON' : '⤳ 表單' }}
          </button>
        </div>

        <template v-if="isKineticLike && editMode === 'form'">
          <div class="row">
            <label>最大射程 (m) <input v-model.number="maxRange" type="number" data-testid="armory-maxrange"></label>
            <label>最小射程 (m) <input v-model.number="minRange" type="number"></label>
            <label>射速/tick <input v-model.number="ratePerTick" type="number" step="0.1"></label>
            <label class="chk"><input v-model="indirectFire" type="checkbox"> 間接射擊</label>
          </div>
          <div v-if="category !== 'MISSILE'" class="row">
            <label>{{ category === 'ARTILLERY' ? '火砲類型' : '武器細分' }}
              <select v-if="category === 'ARTILLERY'" v-model="artilleryKind" data-testid="armory-artillery-kind">
                <option v-for="k in ARTILLERY_KINDS" :key="k" :value="k">{{ ARTILLERY_KIND_LABELS[k] }}</option>
              </select>
              <select v-else v-model="kineticKind" data-testid="armory-kinetic-kind">
                <option v-for="k in KINETIC_KINDS" :key="k" :value="k">{{ KINETIC_KIND_LABELS[k] }}</option>
              </select>
            </label>
          </div>
          <label class="wide">彈種（逗號分隔）
            <input v-model="ammoTypes" data-testid="armory-ammo" placeholder="AMMO_556, AMMO_AP">
          </label>

          <label class="wide">命中率插值法
            <select v-model="phInterp" data-testid="armory-ph-interp">
              <option v-for="m in PH_INTERP_MODES" :key="m" :value="m">{{ PH_INTERP_LABELS[m] }}</option>
            </select>
          </label>
          <div class="sub">
            命中率 Ph（依射程控制點：range_max_m → base_ph）——
            {{ phInterp === 'polynomial' ? '多項式（拉格朗日）曲線穿過全部控制點' : '控制點間線性插值' }}
          </div>
          <div v-for="(b, i) in phBands" :key="`ph${i}`" class="pair">
            <input v-model.number="b.range" type="number" placeholder="range m">
            <input v-model.number="b.ph" type="number" step="0.05" min="0" max="1" placeholder="Ph 0–1">
            <button class="rm" @click="phBands.splice(i, 1)">✕</button>
          </div>
          <button class="add" @click="phBands.push({ range: 0, ph: 0.5 })">＋ Ph 控制點</button>
          <p v-if="phInterp === 'polynomial' && phBands.length < 3" class="warn" data-testid="ph-poly-warn">
            多項式插值需 ≥3 個控制點才有意義（少於 3 點時等同線性）。
          </p>

          <div class="sub">傷害（依裝甲級別：armor_class → 扣血點）</div>
          <div v-for="(r, i) in dmgRows" :key="`dmg${i}`" class="pair">
            <select v-model="r.ac" class="ac-sel" data-testid="armory-armor-class">
              <option v-for="ac in ARMOR_CLASSES" :key="ac" :value="ac">
                {{ ARMOR_CLASS_LABELS[ac] }}（{{ ac }}）
              </option>
              <option v-if="r.ac && !ARMOR_CLASS_LABELS[r.ac]" :value="r.ac">{{ r.ac }}（自訂）</option>
              <option value="__custom__">＋ 自訂級別…</option>
            </select>
            <input
              v-if="r.ac === '__custom__' || (r.ac && !ARMOR_CLASS_LABELS[r.ac])"
              :value="r.ac === '__custom__' ? '' : r.ac"
              placeholder="自訂 armor_class"
              @input="r.ac = ($event.target as HTMLInputElement).value.toUpperCase()"
            >
            <input v-model.number="r.dmg" type="number" placeholder="damage">
            <button class="rm" @click="dmgRows.splice(i, 1)">✕</button>
          </div>
          <button class="add" @click="dmgRows.push({ ac: 'INFANTRY', dmg: 0 })">＋ 傷害級別</button>

          <div class="sub">每發擊殺率 pk（真實化交戰主要傷亡驅動：armor_class → P(kill|hit) 0–1）</div>
          <div v-for="(r, i) in pkRows" :key="`pk${i}`" class="pair">
            <select v-model="r.ac" class="ac-sel">
              <option v-for="ac in ARMOR_CLASSES" :key="ac" :value="ac">{{ ARMOR_CLASS_LABELS[ac] }}（{{ ac }}）</option>
              <option v-if="r.ac && !ARMOR_CLASS_LABELS[r.ac]" :value="r.ac">{{ r.ac }}（自訂）</option>
            </select>
            <input v-model.number="r.pk" type="number" step="0.05" min="0" max="1" placeholder="pk 0–1">
            <button class="rm" @click="pkRows.splice(i, 1)">✕</button>
          </div>
          <button class="add" @click="pkRows.push({ ac: 'INFANTRY', pk: 0.5 })">＋ 擊殺率</button>

          <template v-if="category === 'ARTILLERY'">
            <div class="sub">火砲諸元（間瞄）</div>
            <div class="row">
              <label>散布 CEP (m) <input v-model.number="dispersionCep" type="number"></label>
              <label>致命半徑 (m) <input v-model.number="lethalRadius" type="number"></label>
              <label>每次射擊發數 <input v-model.number="roundsPerMission" type="number"></label>
            </div>
          </template>
          <div v-if="category === 'ARTILLERY'" class="mobility-block">
            <div class="sub">機動性（自走砲填道路/越野速度；牽引砲取消自走）</div>
            <div class="row">
              <label class="chk"><input v-model="canSelfMove" type="checkbox"> 可自走</label>
              <label>機動類型
                <select v-model="mobilityClass">
                  <option v-for="m in MOBILITY_CLASSES" :key="m" :value="m">{{ MOBILITY_CLASS_LABELS[m] }}</option>
                </select>
              </label>
              <label>道路速度 (km/h) <input v-model.number="roadSpeed" type="number" :disabled="!canSelfMove"></label>
              <label>越野速度 (km/h) <input v-model.number="ccSpeed" type="number" :disabled="!canSelfMove"></label>
            </div>
          </div>

          <template v-if="category === 'MISSILE'">
            <div class="sub">飛彈諸元（導引武器）</div>
            <div class="row">
              <label>飛彈類型
                <select v-model="missileKind" data-testid="armory-missile-kind">
                  <option v-for="k in MISSILE_KINDS" :key="k" :value="k">{{ MISSILE_KIND_LABELS[k] }}</option>
                </select>
              </label>
              <label>導引方式
                <select v-model="guidance" data-testid="armory-guidance">
                  <option v-for="g in GUIDANCE_MODES" :key="g" :value="g">{{ GUIDANCE_LABELS[g] }}</option>
                </select>
              </label>
            </div>
            <div class="row">
              <label>尋標器
                <select v-model="seeker">
                  <option v-for="s in SEEKER_TYPES" :key="s" :value="s">{{ SEEKER_LABELS[s] }}</option>
                </select>
              </label>
              <label>戰鬥部
                <select v-model="warhead" data-testid="armory-warhead">
                  <option v-for="w in WARHEAD_TYPES" :key="w" :value="w">{{ WARHEAD_LABELS[w] }}</option>
                </select>
              </label>
            </div>
            <div class="row">
              <label>飛行速度 (m/s) <input v-model.number="flightSpeed" type="number"></label>
              <label>最小接戰距離 (m) <input v-model.number="minEngageRange" type="number"></label>
              <label>抗反制 0–1 <input v-model.number="cmResistance" type="number" step="0.05" min="0" max="1"></label>
              <label class="chk"><input v-model="topAttack" type="checkbox"> 頂攻模式</label>
            </div>
            <div class="row">
              <label class="chk">
                <input v-model="missileManeuverable" type="checkbox" data-testid="armory-maneuverable">
                可變軌（巡弋/末端機動 → 僅判射程）
              </label>
              <label v-if="!missileManeuverable">拋物線頂高比
                <input v-model.number="apexRatio" type="number" step="0.01" min="0" style="width: 4rem">
              </label>
            </div>
            <p v-if="!missileManeuverable" class="sub" style="color:#94a3b8">
              彈道飛彈：接戰須判射程 + 拋物線是否被地形/障礙（含高度）阻隔（低頂高比＝低伸彈道，較易被擋）。
            </p>
          </template>
        </template>

        <template v-else-if="category === 'VEHICLE' && editMode === 'form'">
          <div class="row">
            <label>組員 <input v-model.number="crew" type="number"></label>
            <label>載員數 <input v-model.number="passengerCap" type="number"></label>
            <label>防禦裝甲級別
              <select v-model="vehArmorClass" data-testid="armory-veh-armor-class">
                <option v-for="ac in ARMOR_CLASSES" :key="ac" :value="ac">{{ ARMOR_CLASS_LABELS[ac] }}</option>
              </select>
            </label>
          </div>
          <div class="sub">各面裝甲厚度（RHA 等效 mm）</div>
          <div class="row">
            <label>正面 <input v-model.number="armorFront" type="number"></label>
            <label>側面 <input v-model.number="armorSide" type="number"></label>
            <label>後面 <input v-model.number="armorRear" type="number"></label>
            <label>頂部 <input v-model.number="armorTop" type="number"></label>
          </div>
          <div class="mobility-block">
            <div class="sub">機動性</div>
            <div class="row">
              <label class="chk"><input v-model="canSelfMove" type="checkbox"> 可自走</label>
              <label>機動類型
                <select v-model="mobilityClass">
                  <option v-for="m in MOBILITY_CLASSES" :key="m" :value="m">{{ MOBILITY_CLASS_LABELS[m] }}</option>
                </select>
              </label>
              <label>道路速度 (km/h) <input v-model.number="roadSpeed" type="number" :disabled="!canSelfMove"></label>
              <label>越野速度 (km/h) <input v-model.number="ccSpeed" type="number" :disabled="!canSelfMove"></label>
            </div>
          </div>
        </template>

        <!-- 感測器 -->
        <template v-else-if="category === 'SENSOR' && editMode === 'form'">
          <div class="row">
            <label>感測器類型
              <select v-model="sensorKind" data-testid="armory-sensor-kind">
                <option v-for="k in SENSOR_KINDS" :key="k" :value="k">{{ SENSOR_KIND_LABELS[k] }}</option>
              </select>
            </label>
            <label>最大偵測 (m) <input v-model.number="sensorMaxRange" type="number"></label>
            <label>可辨識距離 (m) <input v-model.number="identifyRange" type="number"></label>
          </div>
          <div class="row">
            <label>視野/掃描扇角 (°) <input v-model.number="fovDeg" type="number" min="0" max="360"></label>
            <label>掃描週期 (tick) <input v-model.number="scanPeriod" type="number" min="0"></label>
            <label>最小可測 RCS (m²) <input v-model.number="minRcs" type="number" step="0.1"></label>
            <label class="chk"><input v-model="sensorPassive" type="checkbox"> 被動（不發射）</label>
          </div>
          <div class="sub">偵測機率曲線（依距離：range_max_m → p_detect 0–1）</div>
          <div v-for="(d, i) in detectCurve" :key="`dc${i}`" class="pair">
            <input v-model.number="d.range" type="number" placeholder="range m">
            <input v-model.number="d.p" type="number" step="0.05" min="0" max="1" placeholder="p 0–1">
            <button class="rm" @click="detectCurve.splice(i, 1)">✕</button>
          </div>
          <button class="add" @click="detectCurve.push({ range: 0, p: 0.5 })">＋ 偵測控制點</button>
        </template>

        <!-- 通信 -->
        <template v-else-if="category === 'COMMS' && editMode === 'form'">
          <div class="row">
            <label>頻段
              <select v-model="commsBand" data-testid="armory-comms-band">
                <option v-for="b in COMMS_BANDS" :key="b" :value="b">{{ COMMS_BAND_LABELS[b] }}</option>
              </select>
            </label>
            <label>頻率 (MHz) <input v-model.number="freqMhz" type="number"></label>
            <label>資料速率 (kbps) <input v-model.number="dataRate" type="number" step="0.1"></label>
          </div>
          <div class="row">
            <label>發射功率 (dBm) <input v-model.number="txPower" type="number"></label>
            <label>接收靈敏度 (dBm) <input v-model.number="rxSens" type="number"></label>
            <label>天線增益 (dBi) <input v-model.number="antGain" type="number" step="0.5"></label>
          </div>
          <div class="row">
            <label class="chk"><input v-model="meshCapable" type="checkbox"> 網狀中繼</label>
            <label class="chk"><input v-model="encrypted" type="checkbox"> 加密</label>
            <label class="chk"><input v-model="leoSatcom" type="checkbox"> 低軌衛星</label>
          </div>
        </template>

        <!-- 後勤 -->
        <template v-else-if="category === 'LOGISTICS' && editMode === 'form'">
          <div class="sub">攜行量（補給類別 → 數量）</div>
          <div v-for="(r, i) in capRows" :key="`cap${i}`" class="pair">
            <select v-model="r.cls" class="ac-sel">
              <option v-for="c in Object.keys(SUPPLY_CLASS_LABELS)" :key="c" :value="c">
                {{ SUPPLY_CLASS_LABELS[c] }}（{{ c }}）
              </option>
              <option v-if="r.cls && !SUPPLY_CLASS_LABELS[r.cls]" :value="r.cls">{{ r.cls }}（自訂）</option>
            </select>
            <input v-model.number="r.amt" type="number" placeholder="數量">
            <button class="rm" @click="capRows.splice(i, 1)">✕</button>
          </div>
          <button class="add" @click="capRows.push({ cls: 'WATER_FOOD', amt: 0 })">＋ 補給類別</button>
          <div class="sub">運輸能力</div>
          <div class="row">
            <label>載員 <input v-model.number="troopCap" type="number" min="0"></label>
            <label>載具槽位 <input v-model.number="vehicleSlots" type="number" min="0"></label>
            <label>裝卸 (tick) <input v-model.number="loadUnloadTicks" type="number" min="0"></label>
            <label class="chk"><input v-model="canTow" type="checkbox"> 可拖曳</label>
          </div>
          <div class="row">
            <label>補給速率/tick <input v-model.number="resupplyRate" type="number" min="0"></label>
            <label>勤務組員 <input v-model.number="logCrew" type="number" min="0"></label>
          </div>
          <div class="mobility-block">
            <div class="sub">機動性</div>
            <div class="row">
              <label class="chk"><input v-model="canSelfMove" type="checkbox"> 可自走</label>
              <label>機動類型
                <select v-model="mobilityClass">
                  <option v-for="m in MOBILITY_CLASSES" :key="m" :value="m">{{ MOBILITY_CLASS_LABELS[m] }}</option>
                </select>
              </label>
              <label>道路速度 (km/h) <input v-model.number="roadSpeed" type="number" :disabled="!canSelfMove"></label>
              <label>越野速度 (km/h) <input v-model.number="ccSpeed" type="number" :disabled="!canSelfMove"></label>
            </div>
          </div>
        </template>

        <!-- 無人機 -->
        <template v-else-if="category === 'DRONE' && editMode === 'form'">
          <div class="row">
            <label>無人機類型
              <select v-model="droneKind" data-testid="armory-drone-kind">
                <option v-for="k in DRONE_KINDS" :key="k" :value="k">{{ DRONE_KIND_LABELS[k] }}</option>
              </select>
            </label>
            <label>感測酬載
              <select v-model="sensorPayload">
                <option v-for="p in SENSOR_PAYLOADS" :key="p" :value="p">{{ SENSOR_PAYLOAD_LABELS[p] }}</option>
              </select>
            </label>
            <label class="chk"><input v-model="isExpendable" type="checkbox"> 消耗性（遊蕩彈藥）</label>
          </div>
          <div class="row">
            <label>續航 (tick) <input v-model.number="enduranceTicks" type="number"></label>
            <label>巡航 (m/s) <input v-model.number="cruiseSpeed" type="number"></label>
            <label>升限 (m) <input v-model.number="serviceCeiling" type="number"></label>
          </div>
          <div class="row">
            <label>資料鏈距離 (m) <input v-model.number="dataLinkRange" type="number"></label>
            <label>酬載 (kg) <input v-model.number="payloadKg" type="number" step="0.1"></label>
          </div>
          <div class="sub">氣象限制</div>
          <div class="row">
            <label>最大風速 (m/s) <input v-model.number="maxWind" type="number"></label>
            <label>最低能見度 (m) <input v-model.number="minVis" type="number"></label>
          </div>
        </template>

        <!-- JSON 檢視/編修（任何類別皆可，如「火力 直射動能」的 JSON 鈕） -->
        <template v-else>
          <label class="wide">
            屬性 JSON（依 weaponeering.schema.json 之 {{ category.toLowerCase() }} 規格）
            <textarea v-model="jsonText" rows="14" data-testid="armory-json" spellcheck="false" />
          </label>
        </template>

        <div class="actions">
          <button class="save" data-testid="armory-save" :disabled="busy" @click="save">
            {{ selectedId ? '儲存變更' : '建立範本' }}
          </button>
        </div>
      </section>
    </div>
  </main>
</template>

<style scoped>
.armory {
  max-width: 60rem;
  margin: 0 auto;
  padding: 1.5rem 1rem;
  color: #e2e8f0;
}
header {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1rem;
}
header h1 {
  font-size: 1.25rem;
  margin: 0;
}
.back {
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: transparent;
  color: #e2e8f0;
  padding: 0.25rem 0.6rem;
  cursor: pointer;
}
.body {
  display: flex;
  gap: 1.25rem;
  align-items: flex-start;
}
.list {
  width: 13rem;
  flex: none;
}
.list .new {
  width: 100%;
  margin-bottom: 0.5rem;
  padding: 0.4rem;
  border: 1px dashed #334155;
  border-radius: 0.3rem;
  background: transparent;
  color: #7dd3fc;
  cursor: pointer;
}
.list ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.list li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.4rem 0.5rem;
  border: 1px solid #1e293b;
  border-radius: 0.3rem;
  cursor: pointer;
}
.list li.sel {
  border-color: #2563eb;
  background: #172554;
}
.list .t-cat {
  color: #64748b;
  font-size: 0.7rem;
}
.list .empty {
  color: #64748b;
  cursor: default;
}
.editor {
  flex: 1;
  border: 1px solid #1e293b;
  border-radius: 0.5rem;
  padding: 1rem;
}
.editor h2 {
  margin: 0 0 0.75rem;
  font-size: 1rem;
  color: #94a3b8;
}
.row {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
  margin-bottom: 0.6rem;
}
label {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  font-size: 0.78rem;
  color: #94a3b8;
}
label.wide {
  margin-bottom: 0.6rem;
}
label.chk {
  flex-direction: row;
  align-items: center;
  gap: 0.35rem;
  align-self: flex-end;
  color: #cbd5e1;
}
input,
select,
textarea {
  background: #0f172a;
  color: #e2e8f0;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  padding: 0.3rem 0.4rem;
  font-size: 0.8rem;
}
label.chk input {
  padding: 0;
}
textarea {
  width: 100%;
  font-family: ui-monospace, monospace;
  resize: vertical;
}
.sub {
  margin: 0.6rem 0 0.35rem;
  font-size: 0.74rem;
  color: #64748b;
}
.pair {
  display: flex;
  gap: 0.4rem;
  margin-bottom: 0.3rem;
}
.pair input {
  flex: 1;
}
.pair .ac-sel {
  flex: 1;
  min-width: 8rem;
}
.warn {
  margin: 0.2rem 0 0.4rem;
  font-size: 0.72rem;
  color: #fbbf24;
}
.mode-toggle {
  align-self: flex-end;
  margin-left: auto;
  padding: 0.3rem 0.6rem;
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: transparent;
  color: #7dd3fc;
  cursor: pointer;
  font-size: 0.75rem;
}
.mode-toggle:hover {
  border-color: #2563eb;
}
.mobility-block {
  margin-top: 0.4rem;
  padding: 0.5rem;
  border: 1px dashed #334155;
  border-radius: 0.35rem;
}
.rm {
  border: none;
  background: transparent;
  color: #f87171;
  cursor: pointer;
}
.add {
  border: 1px solid #334155;
  border-radius: 0.25rem;
  background: transparent;
  color: #7dd3fc;
  cursor: pointer;
  padding: 0.2rem 0.5rem;
  font-size: 0.74rem;
}
.actions {
  margin-top: 1rem;
}
.save {
  padding: 0.45rem 1rem;
  border: 0;
  border-radius: 0.3rem;
  background: #2563eb;
  color: #fff;
  cursor: pointer;
}
.save:disabled {
  opacity: 0.5;
}
</style>
