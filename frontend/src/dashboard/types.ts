export interface DashboardStats {
  req_open?: number | null
  req_trend?: string | null
  daily_done?: number | null
  daily_total?: number | null
  daily_note?: string | null
  monthly_release_total?: number | null
  monthly_release_done?: number | null
  completed_total?: number | null
  display_monthly_release_total?: number | null
  display_monthly_release_done?: number | null
  real_monthly_release_total?: number | null
  real_monthly_release_done?: number | null
  is_demo?: boolean | null
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
  is_demo?: boolean | null
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
  is_demo?: boolean | null
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
  is_demo?: boolean | null
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

export interface DashboardProgressStage {
  key: string
  label: string
  count: number
}

export interface DashboardProgressItem {
  id?: string | null
  code?: string | null
  title?: string | null
  system?: string | null
  kind?: string | null
  status?: string | null
  group?: string | null
  stage?: number | null
  test_points?: string | null
  next_action?: string | null
  updated_at?: string | null
  is_demo?: boolean | null
}

export interface DashboardBoardItem {
  id: string
  code?: string | null
  title?: string | null
  system?: string | null
  status?: string | null
  group: string
  priority?: string | null
  tp_done?: number | null
  tp_total?: number | null
  actual_release_date?: string | null
  next_action?: string | null
  updated_at?: string | null
  is_demo?: boolean | null
}

export interface DashboardProgress {
  stages?: DashboardProgressStage[] | null
  paused?: number | null
  active_total?: number | null
  active?: DashboardProgressItem[] | null
  board?: DashboardBoardItem[] | null
  online_month?: number | null
}

export interface TestPoint {
  id: string
  text: string
  done: boolean
}

export interface RequirementDetail {
  id: string
  code: string
  title: string
  status: string
  group: string
  group_label: string
  stage: number
  priority: string
  category: string
  kind: string
  systems: string[]
  system: string
  actual_release_date: string
  test_points: TestPoint[]
  tp_done: number
  tp_total: number
  next_action: string
  has_local_dir: boolean
  svn_url: string
  updated_at: string
  description: string
}

/** 看板/光谱共用的阶段定义（与 tools/requirement_stages.py 对齐）。 */
export const STAGE_DEFS = [
  { key: 'analysis', label: '分析', color: 'var(--aurora-end)' },
  { key: 'develop', label: '开发', color: 'var(--primary)' },
  { key: 'testing', label: '测试', color: 'var(--warning)' },
  { key: 'release', label: '待上线', color: 'var(--accent-cyan)' },
  { key: 'online', label: '本月上线', color: 'var(--success)' },
] as const

export type StageKey = typeof STAGE_DEFS[number]['key']

export interface DashboardSummary {
  username?: string | null
  greeting?: string | null
  date_line?: string | null
  is_demo?: boolean | null
  stats?: DashboardStats | null
  release?: DashboardRelease | null
  recent?: DashboardRecentItem[] | null
  checklist?: DashboardChecklistItem[] | null
  tools?: DashboardToolItem[] | null
  monthly_release_tasks?: MonthlyReleaseTask[] | null
  progress?: DashboardProgress | null
}
