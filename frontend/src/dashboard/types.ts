export interface DashboardStats {
  req_open?: number | null
  req_trend?: string | null
  daily_done?: number | null
  daily_total?: number | null
  daily_note?: string | null
  monthly_release_total?: number | null
  monthly_release_done?: number | null
  completed_total?: number | null
}

export interface DashboardRelease {
  version?: string | null
  total?: number | null
  done?: number | null
  percent?: number | null
  days_left?: number | null
  date_text?: string | null
  countdown_state?: 'future' | 'today' | 'overdue' | 'unset' | string | null
  target_date?: string | null
}

export interface MonthlyReleaseTask {
  id?: string | null
  code?: string | null
  title?: string | null
  system?: string | null
  status?: string | null
  test_points?: string | null
  actual_release_date?: string | null
  actual_online_date?: string | null
  done?: boolean | null
  nav?: number | null
}

export interface DashboardRecentItem {
  id?: string | null
  code?: string | null
  title?: string | null
  system?: string | null
  actual_release_date?: string | null
  actual_online_date?: string | null
  test_points?: string | null
  status?: 'run' | 'rev' | 'ok' | string | null
  status_label?: string | null
  color?: string | null
  done?: boolean | null
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
  monthly_release_tasks?: MonthlyReleaseTask[] | null
}
