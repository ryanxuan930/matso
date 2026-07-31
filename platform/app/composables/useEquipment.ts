// 編裝（裝備/武器裝載）編輯 API（stage ①）——範本目錄 + 單位裝備增/列/改/刪。
import type { components } from '~/types/api'
import { apiFetch } from '~/composables/useApi'

export type EquipmentTemplate = components['schemas']['EquipmentTemplateView']
export type EquipmentInstance = components['schemas']['EquipmentInstanceView']

export function fetchEquipmentTemplates(): Promise<EquipmentTemplate[]> {
  return apiFetch<EquipmentTemplate[]>('/equipment-templates')
}

export interface TemplateEdit {
  name: string
  category: string
  base_stats: Record<string, unknown>
}
export function createEquipmentTemplate(body: TemplateEdit): Promise<EquipmentTemplate> {
  return apiFetch<EquipmentTemplate>('/equipment-templates', { method: 'POST', body })
}
export function updateEquipmentTemplate(tid: string, body: TemplateEdit): Promise<EquipmentTemplate> {
  return apiFetch<EquipmentTemplate>(`/equipment-templates/${tid}`, { method: 'PUT', body })
}
export async function deleteEquipmentTemplate(tid: string): Promise<void> {
  await apiFetch<unknown>(`/equipment-templates/${tid}`, { method: 'DELETE' })
}
/**
 * 各軍自編權限（白軍開放哪些陣營可自行編裝）——限白軍讀寫。
 *
 * 型別走契約生成的 `OrbatPermissions`，不再就地寫 `{ factions: string[] }`——
 * 那種行內型別是 P4 那一批漂移的縮影：後端加一個欄位，前端永遠不會知道。
 */
export type OrbatPermissions = components['schemas']['OrbatPermissions']

export function fetchOrbatPermissions(sessionId: string): Promise<OrbatPermissions> {
  return apiFetch<OrbatPermissions>(`/sessions/${sessionId}/orbat-permissions`)
}
export function setOrbatPermissions(
  sessionId: string,
  factions: string[],
): Promise<OrbatPermissions> {
  return apiFetch<OrbatPermissions>(`/sessions/${sessionId}/orbat-permissions`, {
    method: 'PUT',
    body: { factions },
  })
}
export function fetchUnitEquipment(sessionId: string, unitId: string): Promise<EquipmentInstance[]> {
  return apiFetch<EquipmentInstance[]>(`/sessions/${sessionId}/units/${unitId}/equipment`)
}
export function addUnitEquipment(
  sessionId: string,
  unitId: string,
  templateId: string,
): Promise<EquipmentInstance> {
  return apiFetch<EquipmentInstance>(`/sessions/${sessionId}/units/${unitId}/equipment`, {
    method: 'POST',
    body: { template_id: templateId },
  })
}
export function editUnitEquipment(
  sessionId: string,
  unitId: string,
  eid: string,
  currentState: Record<string, unknown>,
  quantity?: number, // #30 建制數量（可選）
): Promise<EquipmentInstance> {
  return apiFetch<EquipmentInstance>(`/sessions/${sessionId}/units/${unitId}/equipment/${eid}`, {
    method: 'PATCH',
    body: { current_state: currentState, ...(quantity != null ? { quantity } : {}) },
  })
}
export async function removeUnitEquipment(
  sessionId: string,
  unitId: string,
  eid: string,
): Promise<void> {
  await apiFetch<unknown>(`/sessions/${sessionId}/units/${unitId}/equipment/${eid}`, {
    method: 'DELETE',
  })
}

// ---- 單位屬性（番號/兵科/編制級別/人數/戰力）----

export type UnitEdit = components['schemas']['UnitEdit']
export type UnitEditView = components['schemas']['UnitEditView']

/**
 * 編輯單位屬性。PATCH 語義——**只送要改的欄位**。
 *
 * `health_status` 不在可編清單裡：它是由戰力比導出的顯示值，裁決層每次命中都會
 * 覆寫它。後端對它回 422 而不是靜默忽略，所以這裡也不要幫忙塞。
 *
 * 回應的 `restart_required` 為真時，代表這次改到了「runner 起跑才讀一次」的東西
 * （目前只有 `unit_level`）——要告訴使用者，否則他會以為已經生效。
 */
export function editUnitAttributes(
  sessionId: string,
  unitId: string,
  body: UnitEdit,
): Promise<UnitEditView> {
  return apiFetch<UnitEditView>(`/sessions/${sessionId}/units/${unitId}`, {
    method: 'PATCH',
    body,
  })
}
