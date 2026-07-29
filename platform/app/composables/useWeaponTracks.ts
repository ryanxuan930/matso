/**
 * #95 武器軌跡（**純顯示**）。
 *
 * 紅線：這只是把已裁決的結果畫出來，**絕不回頭影響裁決**——軌跡由 ENGAGEMENT_RESOLVED
 * 事件（後端已裁決完）觸發，前端不做任何命中/可達判定。
 *
 * 端點座標取自「client 本來就看得到的東西」（我方/友軍單位 + 已偵獲的 contact），
 * **刻意不讓後端在事件裡夾帶座標**：夾帶座標等於把全場每次交戰的精確位置送給收得到該事件的
 * 每個人。代價：陣營視角下若看不到某一端（例如未偵獲的射手），該次交戰就不畫——這是正確的
 * 迷霧行為。
 */
import { computed, onBeforeUnmount, ref, watch, type Ref } from 'vue'
import type { Contact, OwnUnit } from '~/composables/useUnits'

const TRACK_TTL_MS = 4000

interface WeaponTrack {
  key: string
  from: [number, number]
  to: [number, number]
  status: string
  born: number
}

/** `events` 為串流事件的累積緩衝（`useSessionStreamStore().events`）。 */
export function useWeaponTracks(
  ownUnits: Ref<OwnUnit[]>,
  contacts: Ref<Contact[]>,
  events: Ref<{ seq?: number; payload?: Record<string, unknown> }[]>,
) {
  const weaponTracks = ref<WeaponTrack[]>([])
  const trackNow = ref(0) // 由計時器推進，驅動淡出（只在有軌跡時跑）
  let trackCursor = 0
  let trackTimer: ReturnType<typeof setInterval> | null = null

  /** 由 id 找地圖上的座標：我方/友軍單位，或已偵獲的 contact。查無 → null（不畫）。 */
  function trackPos(id?: string | null): [number, number] | null {
    if (!id) return null
    const u = ownUnits.value.find((x) => x.id === id)
    if (u) return [u.lng, u.lat]
    const c = contacts.value.find((x) => x.contactId === id)
    return c ? [c.lng, c.lat] : null
  }

  const weaponTrackFc = computed(() => ({
    type: 'FeatureCollection' as const,
    features: weaponTracks.value.map((t) => {
      const age = Math.max(0, trackNow.value - t.born)
      return {
        type: 'Feature' as const,
        properties: {
          status: t.status,
          // 線性淡出；REJECTED 不畫（下方過濾），HIT 較亮、MISS 較淡以便一眼分辨。
          opacity: Math.max(0, 1 - age / TRACK_TTL_MS) * (t.status === 'HIT' ? 0.95 : 0.5),
        },
        geometry: { type: 'LineString' as const, coordinates: [t.from, t.to] },
      }
    }),
  }))

  // 只吃新到的事件（events 是累積緩衝，重看舊事件不該重畫）。
  watch(
    () => events.value.length,
    (len) => {
      if (len < trackCursor) trackCursor = 0 // 緩衝被裁切（MAX_EVENTS）→ 重置游標
      for (const env of events.value.slice(trackCursor)) {
        const p = env.payload ?? {}
        if (p.event_type !== 'ENGAGEMENT_RESOLVED') continue
        const status = String(p.status ?? '')
        if (status === 'REJECTED') continue // 根本沒射出去，不畫
        const from = trackPos(p.initiator_id as string | undefined)
        const to = trackPos(p.target_id as string | undefined)
        if (!from || !to) continue // 有一端看不到 → 不畫（迷霧下的正確行為）
        weaponTracks.value.push({
          key: `${p.initiator_id}-${p.target_id}-${env.seq ?? len}`,
          from,
          to,
          status,
          born: Date.now(),
        })
      }
      trackCursor = len
      // 有軌跡才起計時器（推進淡出 + 到期清除）；清空即停，避免閒置空轉。
      if (weaponTracks.value.length && !trackTimer) {
        trackTimer = setInterval(() => {
          trackNow.value = Date.now()
          weaponTracks.value = weaponTracks.value.filter(
            (t) => trackNow.value - t.born < TRACK_TTL_MS,
          )
          if (!weaponTracks.value.length && trackTimer) {
            clearInterval(trackTimer)
            trackTimer = null
          }
        }, 200)
      }
    },
  )

  onBeforeUnmount(() => {
    if (trackTimer) clearInterval(trackTimer)
  })

  return { weaponTrackFc }
}
