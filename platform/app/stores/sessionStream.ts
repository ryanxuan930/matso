import { defineStore } from 'pinia'
import { apiFetch, useAuthTokens } from '~/composables/useApi'

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

  let ws: WebSocket | null = null
  let sessionId = ''
  let backoff = 500
  let closedByUser = false
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null

  function connect(id: string): void {
    if (import.meta.server) return // WebSocket 僅 client
    sessionId = id
    closedByUser = false
    open()
  }

  function open(): void {
    const { access } = useAuthTokens()
    if (!access.value) return
    status.value = 'connecting'
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
      default:
        if (typeof env.seq === 'number') lastSeq.value = env.seq
        events.value.push(env)
    }
  }

  function scheduleReconnect(): void {
    status.value = 'connecting'
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

  return { status, lastSeq, events, faction, connect, disconnect }
})
