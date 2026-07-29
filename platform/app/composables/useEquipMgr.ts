/**
 * COP 裝備管理面板的狀態——白軍可編任一單位編裝並設定各軍「自編」權限；
 * 一般角色僅在該局開放自編時編本軍單位。
 *
 * 可編範圍與權限開關是兩件事：`equipEditableFactions` 決定**這個人能編誰**，
 * `orbatPerms` 決定**哪些陣營被授權自編**——後者只有白軍改得動，且後端仍是權威。
 */
import { computed, ref, type ComputedRef, type Ref } from 'vue'
import { fetchOrbatPermissions, setOrbatPermissions } from '~/composables/useEquipment'

export function useEquipMgr<G extends { faction: string }>(opts: {
  sessionId: Ref<string>
  canControl: ComputedRef<boolean>
  /** 該局是否開放自編（白軍在編裝面板裡設定）。 */
  orbatEdit: Ref<boolean>
  myFaction: Ref<string>
  unitsByFaction: ComputedRef<G[]>
  toasts: ReturnType<typeof useToasts>
}) {
  const { sessionId, canControl, orbatEdit, myFaction, unitsByFaction, toasts } = opts

  const equipMgr = ref(false)
  const equipUnitId = ref('')
  const orbatPerms = ref<string[]>([])
  const canManageEquip = computed(() => canControl.value || orbatEdit.value)
  // 可編裝單位：白軍見全部（依陣營分組）；一般角色僅本軍（且該局開放自編）。
  const equipEditableFactions = computed(() =>
    canControl.value
      ? unitsByFaction.value
      : unitsByFaction.value.filter((g) => !!myFaction.value && g.faction === myFaction.value),
  )

  async function openEquipMgr() {
    equipMgr.value = true
    equipUnitId.value = ''
    if (canControl.value) {
      orbatPerms.value = (await fetchOrbatPermissions(sessionId.value).catch(() => ({
        factions: [],
      }))).factions
    }
  }
  async function toggleOrbatPerm(f: string) {
    const set = new Set(orbatPerms.value)
    if (set.has(f)) set.delete(f)
    else set.add(f)
    const next = [...set]
    try {
      orbatPerms.value = (await setOrbatPermissions(sessionId.value, next)).factions
      toasts.push({
        severity: 'success',
        title: `自編權限：${orbatPerms.value.join('、') || '（僅白軍）'}`,
        timeoutMs: 2500,
      })
    } catch {
      toasts.push({ severity: 'error', title: '設定自編權限失敗', timeoutMs: 3000 })
    }
  }

  return {
    equipMgr,
    equipUnitId,
    orbatPerms,
    canManageEquip,
    equipEditableFactions,
    openEquipMgr,
    toggleOrbatPerm,
  }
}
