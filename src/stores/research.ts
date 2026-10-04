import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { initialReports } from '../data/reports'
import { generateResearch } from '../services/research'
import type { Report, ResearchRequest } from '../types'

const STORAGE_KEY = 'just-read:reports:v1'

function validReport(value: unknown): value is Report {
  if (!value || typeof value !== 'object') return false
  const r = value as Report
  return typeof r.id === 'string' && typeof r.title === 'string' && typeof r.question === 'string'
    && typeof r.category === 'string' && typeof r.summary === 'string'
    && typeof r.createdAt === 'string' && Number.isFinite(Date.parse(r.createdAt))
    && typeof r.minutes === 'number' && Array.isArray(r.bookmarks) && r.bookmarks.every(b => typeof b === 'string')
    && Array.isArray(r.chart) && r.chart.every(c => c && typeof c.label === 'string' && Number.isFinite(c.value))
    && Array.isArray(r.chapters) && r.chapters.every(c => c && typeof c.id === 'string' && typeof c.title === 'string' && Array.isArray(c.paragraphs) && c.paragraphs.every(p => typeof p === 'string'))
}

export const useResearchStore = defineStore('research', () => {
  const storageWarning = ref('')
  function restore(): Report[] {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (raw === null) return initialReports()
      const parsed: unknown = JSON.parse(raw)
      if (Array.isArray(parsed) && parsed.every(validReport)) return parsed
      storageWarning.value = '本地记录格式异常，已恢复演示报告。'
    } catch { storageWarning.value = '无法读取本地记录，当前内容仅在本次会话中保留。' }
    return initialReports()
  }
  const reports = ref<Report[]>(restore())
  const running = ref(false)
  const step = ref(0)
  const activeQuestion = ref('')
  const error = ref('')
  let controller: AbortController | undefined
  const bookmarkCount = computed(() => reports.value.reduce((total, report) => total + report.bookmarks.length, 0))

  function persist() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(reports.value)); storageWarning.value = '' }
    catch { storageWarning.value = '本地存储不可用或空间不足。当前修改尚未保存，请导出需要保留的报告。' }
  }
  // 保存初始示例，也确保用户第一次添加书签后能恢复完整报告。
  if (!storageWarning.value) persist()

  async function generate(request: ResearchRequest) {
    if (running.value || !request.question.trim()) return
    running.value = true
    error.value = ''
    step.value = 0
    activeQuestion.value = request.question.trim()
    controller = new AbortController()
    try {
      const report = await generateResearch(request, event => { step.value = event.step }, controller.signal)
      reports.value.unshift(report)
      persist()
      return report.id
    } catch (cause) {
      if (!(cause instanceof DOMException && cause.name === 'AbortError')) error.value = '生成失败，请重试。'
    } finally { running.value = false; controller = undefined }
  }

  function cancel() { controller?.abort() }
  function addReport(report: Report) {
    reports.value.unshift(report)
    persist()
  }
  function toggleBookmark(reportId: string, chapterId: string) {
    const report = reports.value.find(item => item.id === reportId)
    if (!report) return
    report.bookmarks = report.bookmarks.includes(chapterId)
      ? report.bookmarks.filter(id => id !== chapterId) : [...report.bookmarks, chapterId]
    persist()
  }

  return { reports, running, step, activeQuestion, error, storageWarning, bookmarkCount, generate, cancel, toggleBookmark, addReport }
})
