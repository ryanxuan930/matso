import { defineStore } from 'pinia'
import { apiFetch, refreshAccessToken, useAuthTokens } from '~/composables/useApi'
import type { components } from '~/types/api'

export type StateSnapshot = components['schemas']['StateSnapshotView']

// WS envelope（contracts/ws_protocol.md）
interface Envelope {
  v: number
  seq?: number
  tick?: number
  type: string
  payload?: Record<string, unknown>
}

type StreamStatus = 'idle' | 'connecting' | 'live' | 'resyncing' | 'closed'

const MAX_BACKOFF_MS = 10_000
const MAX_EVENTS = 1000 // 前端事件緩衝上限，避免長 session 記憶體無限成長（CODE_REVIEW C7）

/** WS 位址：把 apiBase 的 http(s) 換成 ws(s)。 */
function wsUrl(apiBase: string, sessionId: string, token: string): string {
  const base = apiBase.replace(/^http/, 'ws')
  return `${base}/api/v1/sessions/${sessionId}/stream?token=${encodeURIComponent(token)}`
}

export const useSessionStreamStore = defineStore('sessionStream', () => {
  const status = ref<StreamStatus>('idle')
  const lastSeq = ref<number | null>(null)
  const events = ref<Envelope[]>([])
  const faction = ref<string | null>(null)
  const lastTick = ref<number | null>(null) // 最新 sim tick（供 COP 系統牆鐘顯示，#4；rollback 後可非單調）
  // 活模擬（O10.1）：STATE_DIFF 累積的 per-unit 最新欄位（lat/lng/health…）→ COP 據此即時移動圖標。
  const unitPatches = ref<Record<string, Record<string, unknown>>>({})
  // WP-E3：最近一次原子快照。RESYNC 後由 store 抓取，畫面層 watch 它一次性重建全部狀態。
  // 用 ref 而非事件：Vue 在同一 tick 內合併賦值，畫面只會渲染一次（不會閃過半套狀態）。
  const snapshot = ref<StateSnapshot | null>(null)

  let ws: WebSocket | null = null
  let sessionId = ''
  let viewpoint: string | null = null
  let backoff = 500
  let closedByUser = false
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null

  function connect(id: string, asFaction: string | null = null): void {
    if (import.meta.server) return // WebSocket 僅 client
    sessionId = id
    // WP-E3：白軍的視角切換必須跟著進快照請求，否則切到某軍視角後重連會抓回**全知**快照
    // ——那是迷霧漏洞（重連後看到的比正常時多）。
    viewpoint = asFaction
    closedByUser = false
    unitPatches.value = {} // 換 session 清空舊位置
    open()
  }

  /** 取一份原子快照並發佈給畫面層（WP-E3）。回傳是否成功。 */
  async function pullSnapshot(): Promise<boolean> {
    const query = viewpoint ? `?as_faction=${encodeURIComponent(viewpoint)}` : ''
    try {
      const snap = await apiFetch<StateSnapshot>(`/sessions/${sessionId}/state${query}`)
      // 快照即權威：先丟掉所有既有 patch，再以快照的 last_seq 當去重基準。
      // 不清的話，RESYNC 前累積的舊 patch 會蓋在新快照上（顯示回到過去的位置）。
      unitPatches.value = {}
      lastSeq.value = snap.last_seq
      if (typeof snap.tick === 'number') lastTick.value = snap.tick
      snapshot.value = snap
      return true
    } catch {
      return false
    }
  }

  async function open(): Promise<void> {
    const { access } = useAuthTokens()
    if (!access.value) return
    // 先清掉既有 socket 與重連計時器，避免抖動下長出多條併行 WS（CODE_REVIEW C7）。
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (ws) {
      ws.onclose = null // 這是主動汰換，不要觸發重連
      ws.close()
      ws = null
    }
    status.value = 'connecting'
    await refreshAccessToken() // WS token 連線前刷新（短 TTL 下避免 4401 競態）
    if (!access.value) return
    ws = new WebSocket(wsUrl(useRuntimeConfig().public.apiBase as string, sessionId, access.value))

    ws.onopen = () => {
      ws?.send(JSON.stringify({ last_seq: lastSeq.value }))
    }
    ws.onmessage = (ev) => handleMessage(JSON.parse(ev.data as string) as Envelope)
    ws.onclose = () => {
      if (closedByUser) {
        status.value = 'closed'
        return
      }
      scheduleReconnect() // 斷線自動重連，HELLO 帶 last_seq 補償
    }
  }

  async function handleMessage(env: Envelope): Promise<void> {
    // 最新 sim tick（任何帶 tick 的 envelope 都更新；取最新值而非最大，rollback 會使 tick 回退）。
    if (typeof env.tick === 'number') lastTick.value = env.tick
    switch (env.type) {
      case 'WELCOME':
        status.value = 'live'
        backoff = 500
        faction.value = (env.payload?.faction as string) ?? null
        break
      case 'RESYNC_REQUIRED':
        status.value = 'resyncing'
        // ring 缺口過大／崩潰復原後的新串流 → 以原子快照重建（WP-E3）。
        // 失敗才退回 last_seq=null（下次重連當新 client，不 backfill）——過去無論成敗都是這樣，
        // 等於快照白抓了。
        if (!(await pullSnapshot())) lastSeq.value = null
        break
      case 'CLOCK':
        // 閒置心跳：頂層 tick 已於上方更新牆鐘；seq 取單調最大，不塞入事件列。
        if (typeof env.seq === 'number') {
          lastSeq.value = lastSeq.value === null ? env.seq : Math.max(lastSeq.value, env.seq)
        }
        break
      case 'STATE_DIFF': {
        // WP-E3：RESYNC 送出後 server 仍持續推播，快照可能比某些 diff 還舊。
        // 丟棄 seq ≤ 快照 last_seq 者，避免舊 diff 覆蓋新快照。
        if (typeof env.seq === 'number' && lastSeq.value !== null && env.seq <= lastSeq.value) break
        if (typeof env.seq === 'number') {
          lastSeq.value = lastSeq.value === null ? env.seq : Math.max(lastSeq.value, env.seq)
        }
        // 套用單位變動欄位（含 lat/lng）→ COP 即時移動圖標（不塞入事件列，保持事件流乾淨）。
        const units = (env.payload?.units ?? []) as Array<{ id: string } & Record<string, unknown>>
        for (const u of units) {
          unitPatches.value[u.id] = { ...unitPatches.value[u.id], ...u }
        }
        break
      }
      default:
        // lastSeq 取單調最大（C3：雙寫入者下 ring 可能短暫亂序，避免 lastSeq 回退致重連重收）。
        if (typeof env.seq === 'number') {
          lastSeq.value = lastSeq.value === null ? env.seq : Math.max(lastSeq.value, env.seq)
        }
        events.value.push(env)
        if (events.value.length > MAX_EVENTS) events.value.splice(0, events.value.length - MAX_EVENTS)
    }
  }

  function scheduleReconnect(): void {
    status.value = 'connecting'
    if (reconnectTimer) clearTimeout(reconnectTimer) // 不堆疊多個待重連計時器（C7）
    reconnectTimer = setTimeout(open, backoff)
    backoff = Math.min(backoff * 2, MAX_BACKOFF_MS) // 指數退避
  }

  function disconnect(): void {
    closedByUser = true
    if (reconnectTimer) clearTimeout(reconnectTimer)
    ws?.close()
    ws = null
    status.value = 'closed'
  }

  return {
    status,
    lastSeq,
    lastTick,
    events,
    faction,
    unitPatches,
    snapshot,
    connect,
    pullSnapshot,
    disconnect,
  }
})
