// 全域彈出通知（toast，#7）——下令被拒等錯誤以通知呈現詳細原因。純前端、可重用（單例狀態）。
import { ref } from 'vue'

export type ToastSeverity = 'error' | 'warn' | 'success' | 'info'
export interface Toast {
  id: number
  severity: ToastSeverity
  title: string
  detail?: string
  lines?: string[] // 多行細節（如各項預檢失敗原因）
}

// 模組層單例：整個 app 共用一份佇列（僅 client 端 push，SSR 期間恆空）。
const toasts = ref<Toast[]>([])
let seq = 0

export function useToasts() {
  function dismiss(id: number) {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }
  function push(t: Omit<Toast, 'id'> & { timeoutMs?: number }): number {
    const id = ++seq
    const { timeoutMs = 9000, ...rest } = t
    toasts.value = [...toasts.value, { id, ...rest }]
    // 錯誤類不自動消失（timeoutMs=0）；其餘依 timeout 自動關閉。
    if (timeoutMs > 0 && import.meta.client) setTimeout(() => dismiss(id), timeoutMs)
    return id
  }
  return { toasts, push, dismiss }
}
