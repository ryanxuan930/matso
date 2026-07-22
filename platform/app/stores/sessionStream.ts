import { defineStore } from 'pinia'
import { apiFetch, refreshAccessToken, useAuthTokens } from '~/composables/useApi'

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

  let ws: WebSocket | null = null
  let sessionId = ''
  let backoff = 500
  let closedByUser = false
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null

  function connect(id: string): void {
    if (import.meta.server) return // WebSocket 僅 client
    sessionId = id
    closedByUser = false
    unitPatches.value = {} // 換 session 清空舊位置
    open()
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
        // ring 缺口過大 → 全量重同步（單位/狀態套用於後續卡；此處重置 last_seq）
        await apiFetch(`/sessions/${sessionId}/state`).catch(() => undefined)
        lastSeq.value = null
        break
      case 'STATE_DIFF': {
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

  return { status, lastSeq, lastTick, events, faction, unitPatches, connect, disconnect }
})
