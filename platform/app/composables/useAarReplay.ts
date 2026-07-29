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

export function useAarReplay(states: Ref<AarReplayStates | null>, tick: Ref<number>) {
  const playing = ref(false)
  const speed = ref(1)

  /** 累加到 `tick` 的單位狀態。純函數：同樣的 (states, tick) 必得同樣的畫面。 */
  const unitsAt = computed<OwnUnit[]>(() => {
    const s = states.value
    if (!s) return []
    const acc = new Map<string, { lat?: number; lng?: number; health: number }>()
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
      }
    }
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

  return { playing, speed, unitsAt, toggle, stop, step }
}
