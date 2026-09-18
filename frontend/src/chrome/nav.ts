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
  dash_zh?: string
}

export interface NavItem {
  i: number
  zh: string
  en: string
  icon: string
  tip?: string
  dash_zh?: string
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

/**
 * 侧栏的展示分组。业务索引、文案和权限仍来自 NavModel；这个类型只描述
 * 原型侧栏需要的布局组织（首页独立、其余入口以组菜单展示）。
 */
export interface DisplayNavGroup {
  key: string
  zh: string
  en: string
  icon: string
  items: NavChild[]
  dividerBefore?: boolean
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
  if (typeof raw['dash_zh'] === 'string') {
    item.dash_zh = raw['dash_zh']
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
      if (typeof (child as Record<string, unknown>)['tip'] === 'string') {
        out.tip = (child as Record<string, unknown>)['tip'] as string
      }
      if (typeof (child as Record<string, unknown>)['dash_zh'] === 'string') {
        out.dash_zh = (child as Record<string, unknown>)['dash_zh'] as string
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

const GROUP_ICON_BY_KEY: Record<string, string> = {
  delivery: 'rocket',
  ops: 'terminal',
  devtools: 'code',
  personal: 'book',
}

function asLeaf(item: NavItem): NavChild[] {
  if (item.children?.length) {
    return item.children.map((child) => ({ ...child }))
  }
  return [{
    i: item.i,
    zh: item.zh,
    en: item.en,
    icon: item.icon,
    ...(item.tip ? { tip: item.tip } : {}),
    ...(item.dash_zh ? { dash_zh: item.dash_zh } : {}),
  }]
}

function groupIcon(group: NavGroup, parent: NavItem | undefined): string {
  if (parent?.children?.some((child) => child.icon === 'spark')) {
    return 'spark'
  }
  return GROUP_ICON_BY_KEY[group.key ?? ''] ?? parent?.icon ?? group.items[0]?.icon ?? 'spark'
}

/**
 * 由唯一的 navModel 派生 Prism 侧栏展示组。
 *
 * 这里不维护页面索引或权限清单：父项 children、叶子项和 settings 都只引用
 * Python 提供的数据。原型的“设计展板”因此不会凭空出现在生产侧栏。
 */
export function deriveDisplayNav(model: NavModel | null): {
  home: NavChild | null
  groups: DisplayNavGroup[]
} {
  if (!model) {
    return { home: null, groups: [] }
  }

  let home: NavChild | null = null
  const groups: DisplayNavGroup[] = []

  for (const sourceGroup of model.groups) {
    const sourceItems = sourceGroup.items
    for (const item of sourceItems) {
      if (item.i === 0 && !item.children) {
        home = asLeaf(item)[0] ?? null
        continue
      }

      if (item.children?.length) {
        groups.push({
          key: `${sourceGroup.key ?? 'group'}:${item.i}`,
          zh: item.zh,
          en: item.en,
          icon: groupIcon(sourceGroup, item),
          items: asLeaf(item),
          dividerBefore: false,
        })
        continue
      }

      if (!groups.some((group) => group.key === sourceGroup.key)) {
        groups.push({
          key: sourceGroup.key ?? `group-${groups.length}`,
          zh: sourceGroup.zh,
          en: sourceGroup.en,
          icon: groupIcon(sourceGroup, undefined),
          items: [],
          dividerBefore: sourceGroup.key === 'delivery' || sourceGroup.key === 'devtools',
        })
      }
      const group = groups.find((candidate) => candidate.key === sourceGroup.key)
      if (group) {
        group.items.push(...asLeaf(item))
      }
    }
  }

  // The prototype separates the home launcher from its first group. The two
  // secondary separators are carried by display metadata, not business data.
  if (groups[0]) {
    groups[0].dividerBefore = true
  }
  return { home, groups }
}

export function displayGroupContains(group: DisplayNavGroup, current: number): boolean {
  return group.items.some((item) => item.i === current)
}
