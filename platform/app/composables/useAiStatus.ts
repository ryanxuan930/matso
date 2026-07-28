// #79 AI 決策心跳狀態（COP「思考中／下一次決策倒數」）。
// 伺服端每 8s 權威重抓；本地每秒遞減 idle 倒數以平滑顯示。faction-scoped：後端已過濾，一般角色只回己方。
import { onScopeDispose, ref } from 'vue'
import type { components } from '~/types/api'
import { apiFetch } from '~/composables/useApi'

export type AiFactionStatus = components['schemas']['AiFactionStatus']

const POLL_MS = 8_000

export function useAiStatus(sessionId: () => string) {
  const factions = ref<AiFactionStatus[]>([])
  let poll: ReturnType<typeof setInterval> | null = null
  let tick: ReturnType<typeof setInterval> | null = null

  async function fetchStatus() {
    try {
      const r = await apiFetch<{ factions: AiFactionStatus[] }>(
        `/sessions/${sessionId()}/ai-status`,
      )
      factions.value = r.factions ?? []
    } catch {
      // 無 AI 指派 / 非參與者 / 尚未起跑 → 靜默隱藏狀態列。
      factions.value = []
    }
  }

  function start() {
    if (!import.meta.client || poll) return
    void fetchStatus()
    poll = setInterval(() => void fetchStatus(), POLL_MS)
    tick = setInterval(() => {
      // 本地平滑倒數（下次權威重抓會校正）。
      for (const f of factions.value) {
        if (f.state === 'idle' && typeof f.seconds_until_next === 'number') {
          f.seconds_until_next = Math.max(0, f.seconds_until_next - 1)
        }
      }
    }, 1_000)
  }

  function stop() {
    if (poll) clearInterval(poll)
    if (tick) clearInterval(tick)
    poll = tick = null
  }

  onScopeDispose(stop)
  return { factions, start, stop, fetchStatus }
}

/** 秒 → mm:ss（供倒數顯示）。 */
export function formatCountdown(seconds: number | null | undefined): string {
  const s = Math.max(0, Math.round(seconds ?? 0))
  const mm = String(Math.floor(s / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${mm}:${ss}`
}
