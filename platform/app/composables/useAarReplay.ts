/**
 * AAR 地圖重播（WP-D6.1）——把「靜態底本 + 逐 tick 差異」累加成某一 tick 的地圖畫面，
 * 外加播放/暫停/倍速。
 *
 * 後端刻意只回差異（帳本本身就是這個形狀），所以拖時間軸是**本地重算**、不回後端：
 * 拖曳每動一格就發一次請求，網路來回會讓滑桿卡住。
 *
 * ⚠ 這裡的 `setInterval` 是**播放動畫**，不是模擬邏輯——紅線 1（禁用牆鐘）管的是
 * `core/` 的推演，前端播放器本來就該用真實時間走。重播內容本身完全來自帳本，
 * 與牆鐘無關（同一份帳本、同一個 tick，畫面必然相同）。
 */
import { computed, onBeforeUnmount, ref, watch, type Ref } from 'vue'
import type { AarReplayStates } from '~/composables/useAar'
import type { OwnUnit } from '~/composables/useUnits'

/** 整場重播在 1× 倍速下走完的目標秒數——與 session 長短無關，體感一致。 */
const FULL_PLAY_SECONDS = 30
const TICK_MS = 100

/** 累加後的單一單位狀態（`unitsAt` 與 `rosterAt` 共用的中間值）。 */
interface UnitAcc {
  lat?: number
  lng?: number
  /** 作戰效能%（0–100）。 */
  health: number
  /** 戰力點（人員/平台數量級）。**只有聚合交戰會記**，個體交戰恆為 undefined。 */
  strength?: number
}

/**
 * 重播清單的一列（檢討會上「這是哪一支部隊、當時剩多少」的那張表）。
 *
 * 為什麼不塞進 `OwnUnit`：`OwnUnit` 是地圖符號的輸入，沒有 `strength` 這一欄
 * （效能% 與戰力點量綱不同，見 `useAar.AarReplayChange`），硬塞會讓地圖把
 * 「剩 420 人」畫成「血量 420%」——後端 `aar/replay.py` 就是修過這個錯才把兩者分家。
 */
export interface AarReplayRosterRow {
  id: string
  designation: string
  faction: string
  unitLevel?: string
  health: number
  strength?: number
  authorizedStrength?: number
  /** 該 tick 是否畫得出來（無座標紀錄的單位不會出現在地圖上，清單要交代去向）。 */
  onMap: boolean
}

export function useAarReplay(states: Ref<AarReplayStates | null>, tick: Ref<number>) {
  const playing = ref(false)
  const speed = ref(1)

  /** 累加到 `tick` 的單位狀態。純函數：同樣的 (states, tick) 必得同樣的畫面。 */
  const stateAt = computed<Map<string, UnitAcc>>(() => {
    const s = states.value
    const acc = new Map<string, UnitAcc>()
    if (!s) return acc
    for (const u of s.units) {
      acc.set(u.id, {
        lat: u.base_lat ?? undefined,
        lng: u.base_lng ?? undefined,
        health: u.base_health,
      })
    }
    for (const f of s.frames) {
      if (f.tick > tick.value) break
      for (const c of f.changes) {
        const cur = acc.get(c.unit_id)
        if (!cur) continue // 帳本提到但不在單位表（已刪）——略過，不要憑空生一個圖標
        if (c.lat !== undefined) cur.lat = c.lat
        if (c.lng !== undefined) cur.lng = c.lng
        if (c.health !== undefined) cur.health = c.health
        // 戰力點過去在這裡被整個丟掉：後端逐 tick 送 `strength`（聚合交戰的權威後態），
        // 前端只讀 health，於是「營級單位打到剩三成」在檢討會上完全看不出來，
        // 只看得到一個掉到 0 的效能%（效能曲線在戰力比 0.30 就歸零）。
        if (c.strength !== undefined) cur.strength = c.strength
      }
    }
    return acc
  })

  /** 地圖符號輸入。與 COP（`useCopUnits.realAsOwn`）帶同一組欄位，兩邊外觀才會一致。 */
  const unitsAt = computed<OwnUnit[]>(() => {
    const s = states.value
    if (!s) return []
    const acc = stateAt.value
    const out: OwnUnit[] = []
    for (const u of s.units) {
      const st = acc.get(u.id)
      // 沒有座標就畫不出來（該單位從頭到尾沒被記過位置，且 DB 也無值）。
      if (!st || st.lat === undefined || st.lng === undefined) continue
      out.push({
        id: u.id,
        faction: (u.faction as OwnUnit['faction']) ?? 'BLUE',
        lat: st.lat,
        lng: st.lng,
        // APP-6A Field T：**沒有它地圖上就是一排無名方塊**，講評時無法指認部隊。
        // 後端一直有回 designation（`api/aar.py` 的 rows），只是從沒被讀進來。
        // 番號理論上必填（DB NOT NULL），退到 id 是為了寧可難看也不要無名。
        designation: u.designation || u.id,
        // APP-6A Field B：階層符號（SIDC 第 12 位）。缺它則連/營/旅在圖上長得一模一樣。
        unitLevel: u.unit_level,
        // AAR 是事後檢討，迷霧已揭；通聯狀態不在重播範圍內，一律以 ONLINE 呈現，
        // 免得沿用 COP 的虛影樣式讓人誤以為「當時失聯」。
        comms: 'ONLINE' as OwnUnit['comms'],
        lastReportedTick: tick.value,
        health: st.health,
        isFixed: u.is_fixed,
      })
    }
    return out
  })

  /** 該 tick 的部隊清單（含畫不出來的單位）——地圖只能表達位置，戰力要靠這張表。 */
  const rosterAt = computed<AarReplayRosterRow[]>(() => {
    const s = states.value
    if (!s) return []
    const acc = stateAt.value
    const rows: AarReplayRosterRow[] = s.units.map((u) => {
      const st = acc.get(u.id)
      return {
        id: u.id,
        designation: u.designation || u.id,
        faction: u.faction,
        unitLevel: u.unit_level,
        health: st?.health ?? u.base_health,
        strength: st?.strength,
        authorizedStrength: u.authorized_strength ?? undefined,
        onMap: st?.lat !== undefined && st?.lng !== undefined,
      }
    })
    // 同陣營排在一起、番號有序——檢討會照著念，順序每次都要一樣。
    rows.sort(
      (a, b) =>
        a.faction.localeCompare(b.faction) ||
        a.designation.localeCompare(b.designation, 'zh-Hant'),
    )
    return rows
  })

  /** 每次推進的 tick 數：讓整場在 FULL_PLAY_SECONDS 內走完（至少 1）。 */
  const step = computed(() => {
    const max = states.value?.max_tick ?? 0
    return Math.max(1, Math.ceil(max / ((FULL_PLAY_SECONDS * 1000) / TICK_MS)))
  })

  let timer: ReturnType<typeof setInterval> | null = null
  function stop() {
    if (timer) clearInterval(timer)
    timer = null
    playing.value = false
  }
  function play() {
    const max = states.value?.max_tick ?? 0
    if (!max) return
    if (tick.value >= max) tick.value = 0 // 播完再按＝從頭
    playing.value = true
    timer = setInterval(() => {
      tick.value = Math.min(max, tick.value + step.value * speed.value)
      if (tick.value >= max) stop()
    }, TICK_MS)
  }
  function toggle() {
    if (playing.value) stop()
    else play()
  }
  // 換倍速時重起計時器，免得要等下一拍才生效。
  watch(speed, () => {
    if (playing.value) {
      stop()
      play()
    }
  })
  onBeforeUnmount(stop)

  return { playing, speed, unitsAt, rosterAt, toggle, stop, step }
}
