import type { Report, ResearchRequest, ResearchTask, SourceMaterial, TaskRun, ReportVersion, Workflow } from '../types'

export const generationSteps = ['A · 目标与材料', 'B · 研究与计算', 'C · 阅读与图解', 'D · 审查与交付']

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message) }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api/v1${path}`, {
      ...options, credentials: 'same-origin', signal: options.signal ?? AbortSignal.timeout(15000),
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
    })
  } catch { throw new ApiError(0, 'NETWORK_ERROR', '无法连接服务。任务记录已保留，请重试连接。') }
  const value = await response.json().catch(() => null)
  if (!response.ok) throw new ApiError(response.status, value?.error?.code ?? 'HTTP_ERROR', value?.error?.message ?? `请求失败（${response.status}）`)
  if (value === null) throw new ApiError(502, 'INVALID_RESPONSE', '服务响应格式无效，请重试。')
  return value as T
}

export const establishSession = () => api<{ ready: boolean; mode: string }>('/health')
export const listReports = () => api<{ reports: Report[] }>('/reports')
export const getReport = (id: string) => api<Report>(`/reports/${encodeURIComponent(id)}`)
export const createTask = (request: ResearchRequest, key: string) => api<ResearchTask>('/research-tasks', {
  method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(request),
})
export const getTask = (id: string) => api<ResearchTask>(`/research-tasks/${encodeURIComponent(id)}`)
export const cancelTask = (id: string) => api<ResearchTask>(`/research-tasks/${encodeURIComponent(id)}/cancel`, { method: 'POST' })
export const saveBookmarks = (id: string, bookmarks: string[]) => api<Report>(`/reports/${encodeURIComponent(id)}/bookmarks`, {
  method: 'PUT', body: JSON.stringify({ bookmarks }),
})
export const importReport = (report: Report) => api<Report>('/reports/import', { method: 'POST', body: JSON.stringify(report) })
export const listSources = () => api<{ sources: SourceMaterial[] }>('/sources')
export const getSource = (id: string) => api<SourceMaterial>(`/sources/${encodeURIComponent(id)}`)
export const addSource = (body: { name: string; media_type: string; content_base64: string }) => api<SourceMaterial>('/sources', { method: 'POST', body: JSON.stringify(body), signal: AbortSignal.timeout(240000) })
export const taskRuns = (id: string) => api<{ runs: TaskRun[] }>(`/research-tasks/${encodeURIComponent(id)}/runs`)
export const taskAction = (id: string, action: 'pause' | 'resume' | 'retry', from_stage?: string) => api<ResearchTask>(`/research-tasks/${encodeURIComponent(id)}/actions`, { method: 'POST', body: JSON.stringify({ action, ...(from_stage ? { from_stage } : {}) }) })
export const listVersions = (id: string) => api<{ versions: ReportVersion[] }>(`/reports/${encodeURIComponent(id)}/versions`)
export const getVersion = (id: string, version: number) => api<Report>(`/reports/${encodeURIComponent(id)}/versions/${version}`)
export const getWorkflow = (id: string, version: number) => api<Workflow>(`/reports/${encodeURIComponent(id)}/workflow?version=${version}`)
export const saveVersion = (id: string, base_version: number, report: Report) => api<Report>(`/reports/${encodeURIComponent(id)}/versions`, { method: 'POST', body: JSON.stringify({ base_version, report }) })
export const reportAction = (id: string, action: string, fields: Record<string, unknown> = {}) => api<Record<string, unknown>>(`/reports/${encodeURIComponent(id)}/actions`, { method: 'POST', body: JSON.stringify({ action, ...fields }), signal: AbortSignal.timeout(240000) })
export const getSharedReport = (token: string) => api<Report>(`/shared/${encodeURIComponent(token)}`)
export async function downloadVersion(id: string, version: number, format: 'html' | 'zip') {
  const response = await fetch(`/api/v1/reports/${encodeURIComponent(id)}/export?version=${version}&format=${format}`, { credentials: 'same-origin' })
  if (!response.ok) {
    const value = await response.json().catch(() => null)
    throw new ApiError(response.status, value?.error?.code ?? 'EXPORT_FAILED', value?.error?.message ?? '固定版本导出失败。')
  }
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = `justread-${id}-v${version}.${format}`; anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 10000)
}

export function validReport(value: unknown): value is Report {
  if (!value || typeof value !== 'object') return false
  const r = value as Report
  return ['demo', 'generated', 'import'].includes(r.source)
    && typeof r.id === 'string' && typeof r.title === 'string' && typeof r.question === 'string'
    && typeof r.category === 'string' && typeof r.summary === 'string'
    && typeof r.createdAt === 'string' && Number.isFinite(Date.parse(r.createdAt))
    && Number.isFinite(r.minutes) && Array.isArray(r.bookmarks) && r.bookmarks.every(b => typeof b === 'string')
    && Array.isArray(r.chart) && r.chart.every(c => c && typeof c.label === 'string' && Number.isFinite(c.value))
    && (!r.chart.length || !!r.chartMeta && ['title', 'unit', 'note'].every(k => typeof r.chartMeta?.[k as keyof NonNullable<Report['chartMeta']>] === 'string'))
    && Array.isArray(r.chapters) && r.chapters.every(c => c && typeof c.id === 'string' && typeof c.title === 'string' && Array.isArray(c.paragraphs) && c.paragraphs.every(p => typeof p === 'string'))
}

export function requireReport(value: unknown): Report {
  if (!validReport(value)) throw new ApiError(502, 'INVALID_REPORT', '报告结构无效，未替换当前报告。')
  return value
}

export function reportNotice(report: Report): string {
  if (report.source === 'import') return '本地导入文档 · 仅提取文字，原始图片与排版未保留。'
  if (report.source === 'demo') return '演示报告 · 固定模板生成，未经检索核验。图表数据为虚构示例。'
  return 'AI 生成报告 · 请结合正文来源核对关键判断；未提供数据时不生成图表。'
}
