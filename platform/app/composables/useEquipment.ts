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
): Promise<EquipmentInstance> {
  return apiFetch<EquipmentInstance>(`/sessions/${sessionId}/units/${unitId}/equipment/${eid}`, {
    method: 'PATCH',
    body: { current_state: currentState },
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
