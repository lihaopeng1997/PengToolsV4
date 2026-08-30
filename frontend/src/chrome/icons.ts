/**
 * 本地静态 SVG symbol id 映射（与 legacy chrome.html 一致）。
 * icon 名称继续兼容 navModel.icon 字段；绝不从 Python/网络接收 SVG HTML。
 */

export const ICON_ID_OVERRIDES: Record<string, string> = {
  chev: 'i-chev',
}

export function iconId(name: string): string {
  return ICON_ID_OVERRIDES[name] ?? `i-${name}`
}
