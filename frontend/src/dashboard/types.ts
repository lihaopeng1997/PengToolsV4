export interface DashboardStats {
  req_open?: number | null
  req_trend?: string | null
  daily_done?: number | null
  daily_total?: number | null
  daily_note?: string | null
}

export interface DashboardRelease {
  version?: string | null
  total?: number | null
  done?: number | null
  percent?: number | null
  days_left?: number | null
  date_text?: string | null
}

export interface DashboardRecentItem {
  code?: string | null
  title?: string | null
  status?: 'run' | 'rev' | 'ok' | string | null
  color?: string | null
  nav?: number | null
}

export interface DashboardChecklistItem {
  t: string
  color?: string | null
  mini?: string | null
}

export interface DashboardToolItem {
  i: number
  zh: string
  ds?: string | null
  icon: string
  grad?: string | null
}

export interface DashboardSummary {
  username?: string | null
  greeting?: string | null
  date_line?: string | null
  stats?: DashboardStats | null
  release?: DashboardRelease | null
  recent?: DashboardRecentItem[] | null
  checklist?: DashboardChecklistItem[] | null
  tools?: DashboardToolItem[] | null
}
