/**
 * 本地静态 SVG symbol id 显式映射。
 *
 * role 名称的权威来源是 ui/navigation_model.py 的 icon_role 字段（数据库子项
 * 统一为 'database'，AI 子项为 'chat'/'spark'）。映射必须显式逐项列出——
 * role 命名与 sprite id 不一致（database→db、requirements→req、release→rocket、
 * doc-update→docsync、operations→term、shield-key→key 等），漏映射即图标缺失。
 * 未知 role 回退到中性本地图标 i-spark 并 console.warn 一次，绝不让菜单留空。
 */

const ROLE_TO_SYMBOL: Record<string, string> = {
  home: 'i-home',
  database: 'i-db',
  chat: 'i-chat',
  requirements: 'i-req',
  release: 'i-rocket',
  'doc-update': 'i-docsync',
  'daily-report': 'i-daily',
  search: 'i-search',
  operations: 'i-term',
  'shield-key': 'i-key',
  'api-debug': 'i-plug',
  json: 'i-braces',
  'document-id': 'i-id',
  vin: 'i-car',
  learning: 'i-book',
  settings: 'i-gear',
  gear: 'i-gear',
  workbench: 'i-spark',
  spark: 'i-spark',
  chev: 'i-chev',
}

const FALLBACK_SYMBOL = 'i-spark'
const warnedRoles = new Set<string>()

export function iconId(name: string): string {
  const mapped = ROLE_TO_SYMBOL[name]
  if (mapped) {
    return mapped
  }
  if (!warnedRoles.has(name)) {
    warnedRoles.add(name)
    console.warn(`[chrome] unknown nav icon role "${name}", falling back to ${FALLBACK_SYMBOL}`)
  }
  return FALLBACK_SYMBOL
}
