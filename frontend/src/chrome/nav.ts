/**
 * HomeBridge.navModel() JSON 的最小类型与结构验证。
 *
 * Schema 权威在 Python：main_window._build_web_nav_model()（ui/navigation_model.py）。
 * 本文件只做消费端最小结构校验，绝不修改 schema。
 */

export interface NavChild {
  i: number
  zh: string
  en: string
  icon: string
  dia?: string
  tip?: string
}

export interface NavItem {
  i: number
  zh: string
  en: string
  icon: string
  tip?: string
  children?: NavChild[]
}

export interface NavGroup {
  key?: string
  zh: string
  en: string
  items: NavItem[]
}

export interface NavModel {
  current?: number
  settings?: NavItem
  groups: NavGroup[]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function requireInt(value: unknown, where: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`navModel 结构无效：${where}.i 必须是数字`)
  }
  return value
}

function requireStr(value: unknown, where: string, field: string): string {
  if (typeof value !== 'string') {
    throw new Error(`navModel 结构无效：${where}.${field} 必须是字符串`)
  }
  return value
}

function validateItem(raw: unknown, where: string, allowChildren: boolean): NavItem {
  if (!isRecord(raw)) {
    throw new Error(`navModel 结构无效：${where} 必须是对象`)
  }
  const item: NavItem = {
    i: requireInt(raw['i'], where),
    zh: requireStr(raw['zh'], where, 'zh'),
    en: requireStr(raw['en'] ?? '', where, 'en'),
    icon: requireStr(raw['icon'] ?? '', where, 'icon'),
  }
  if (typeof raw['tip'] === 'string') {
    item.tip = raw['tip']
  }
  if (raw['children'] !== undefined) {
    if (!allowChildren || !Array.isArray(raw['children'])) {
      throw new Error(`navModel 结构无效：${where}.children 必须是数组`)
    }
    item.children = raw['children'].map((child, idx) => {
      const c = validateItem(child, `${where}.children[${idx}]`, false)
      const out: NavChild = { i: c.i, zh: c.zh, en: c.en, icon: c.icon }
      if (typeof (child as Record<string, unknown>)['dia'] === 'string') {
        out.dia = (child as Record<string, unknown>)['dia'] as string
      }
      return out
    })
  }
  return item
}

/**
 * 解析并最小校验 navModel JSON。任何失败抛错（调用方不得据此调用 pageReady）。
 */
export function parseNavModel(modelJson: string): NavModel {
  const data: unknown = JSON.parse(modelJson)
  if (!isRecord(data)) {
    throw new Error('navModel 结构无效：根必须是对象')
  }
  if (!Array.isArray(data['groups'])) {
    throw new Error('navModel 结构无效：缺少 groups 数组')
  }
  const groups = (data['groups'] as unknown[]).map((rawGroup, gi) => {
    const where = `groups[${gi}]`
    if (!isRecord(rawGroup)) {
      throw new Error(`navModel 结构无效：${where} 必须是对象`)
    }
    if (!Array.isArray(rawGroup['items'])) {
      throw new Error(`navModel 结构无效：${where}.items 必须是数组`)
    }
    const group: NavGroup = {
      zh: requireStr(rawGroup['zh'] ?? '', where, 'zh'),
      en: requireStr(rawGroup['en'] ?? '', where, 'en'),
      items: (rawGroup['items'] as unknown[]).map((it, ii) => validateItem(it, `${where}.items[${ii}]`, true)),
    }
    if (typeof rawGroup['key'] === 'string') {
      group.key = rawGroup['key']
    }
    return group
  })

  const model: NavModel = { groups }
  if (typeof data['current'] === 'number') {
    model.current = data['current']
  }
  if (data['settings'] !== undefined) {
    model.settings = validateItem(data['settings'], 'settings', false)
  }
  return model
}
