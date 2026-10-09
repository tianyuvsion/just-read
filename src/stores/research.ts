import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { Report, ResearchRequest, ResearchTask } from '../types'
import { ApiError, establishSession, listReports, getReport, createTask, getTask, cancelTask, saveBookmarks, importReport, requireReport, validReport, taskAction } from '../services/research'

const STORAGE_KEY = 'just-read:reports:v1'
const PENDING_KEY = 'just-read:pending:v1'
const RECENT_TASK_KEY = 'just-read:recent-task:v1'
interface PendingTask { key: string; request: ResearchRequest; taskId: string | null; cancelRequested: boolean; createdAt?: string; replayBlocked?: boolean }
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

export const useResearchStore = defineStore('research', () => {
  const storageWarning = ref('')
  const error = ref('')
  const ready = ref(false)
  const generationReady = ref(false)
  const initializing = ref(false)
  const step = ref(0)
  const message = ref('')
  const completedReportId = ref('')
  const bookmarkBusy = ref<string[]>([])
  const lastTask = ref<ResearchTask | null>(null)
  const actionBusy = ref(false)
  function restore(): Report[] {
    try {
      const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')
      if (!Array.isArray(raw)) throw new Error('Invalid cache')
      return raw.map(r => ({ ...r, source: r.source ?? 'demo',
        ...(r.chart?.length && !r.chartMeta && (!r.source || r.source === 'demo')
          ? { chartMeta: { title: '历史演示评分', unit: '分', note: '历史演示数据，不用于决策。' } } : {}),
      })).filter(validReport)
    } catch { storageWarning.value = '本地缓存无法读取，正在从服务端恢复报告。'; return [] }
  }
  function readPending(): PendingTask | null {
    try {
      const p = JSON.parse(localStorage.getItem(PENDING_KEY) ?? 'null')
      if (!p) return null
      if (typeof p.key !== 'string' || !p.key || typeof p.request?.question !== 'string' || !['brief', 'deep'].includes(p.request?.depth)
        || !(p.taskId === null || typeof p.taskId === 'string') || typeof p.cancelRequested !== 'boolean') throw new Error('Invalid pending task')
      return p
    } catch { error.value = '未完成任务记录损坏，未自动重新提交。'; return null }
  }
  const reports = ref<Report[]>(restore())
  const pending = ref<PendingTask | null>(readPending())
  function readRecent(): PendingTask | null {
    try { const value = JSON.parse(localStorage.getItem(RECENT_TASK_KEY) ?? 'null'); return value && typeof value.taskId === 'string' && typeof value.key === 'string' && typeof value.request?.question === 'string' ? value : null }
    catch { return null }
  }
  let lastContext: PendingTask | null = pending.value ?? readRecent()
  const taskId = computed(() => pending.value?.taskId ?? lastTask.value?.id ?? '')
  const paused = computed(() => lastTask.value?.status === 'paused' && !!pending.value)
  const running = computed(() => pending.value !== null)
  const pendingBlocked = computed(() => pending.value?.replayBlocked ?? false)
  const cancelling = computed(() => pending.value?.cancelRequested ?? false)
  const activeQuestion = computed(() => pending.value?.request.question ?? '')
  const bookmarkCount = computed(() => reports.value.reduce((n, r) => n + r.bookmarks.length, 0))
  const backendIds = new Set<string>()
  let initialization: Promise<void> | undefined
  let execution: Promise<void> | undefined

  function persistReports() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(reports.value)); storageWarning.value = '' }
    catch { storageWarning.value = '本地缓存空间不足；已提交的报告仍保存在服务端。' }
  }
  function persistPending(value: PendingTask | null) {
    // Call only inside the short storage lock; never hold it during HTTP calls.
    try { if (value) localStorage.setItem(PENDING_KEY, JSON.stringify(value)); else localStorage.removeItem(PENDING_KEY) }
    catch { throw new Error('无法保存任务恢复记录。请允许浏览器存储后重试，尚未发送此操作。') }
    pending.value = value
  }
  function withPendingLock<T>(change: () => T): Promise<T> {
    return navigator.locks ? navigator.locks.request(PENDING_KEY, change) : Promise.resolve(change())
  }
  function syncPending() {
    const current = readPending()
    if (current?.key !== pending.value?.key) { step.value = 0; message.value = ''; error.value = '' }
    pending.value = current
    return current
  }
  function updatePending(key: string, patch: Partial<Pick<PendingTask, 'taskId' | 'cancelRequested' | 'replayBlocked'>> | null) {
    return withPendingLock(() => {
      const current = syncPending()
      if (current?.key !== key) return false
      if (!patch) persistPending(null)
      else persistPending({ ...current, ...patch,
        taskId: current.taskId ?? patch.taskId ?? null,
        cancelRequested: current.cancelRequested || !!patch.cancelRequested,
        replayBlocked: current.replayBlocked || !!patch.replayBlocked,
      })
      return true
    })
  }
  window.addEventListener('storage', event => {
    if (event.storageArea !== localStorage) return
    if (event.key === PENDING_KEY || event.key === null) {
      syncPending()
      if (pending.value && ready.value && !execution) void resume()
    }
    if (event.key === STORAGE_KEY) {
      const cached = restore()
      // New reports in another tab have already been saved through the API.
      const known = new Set(reports.value.map(report => report.id))
      cached.forEach(report => { if (!known.has(report.id)) backendIds.add(report.id) })
      reports.value = cached
    }
  })
  function upsert(report: Report, preserveBookmarks = false) {
    const valid = requireReport(report)
    const old = reports.value.find(r => r.id === valid.id)
    const merged = preserveBookmarks && old ? { ...valid, bookmarks: old.bookmarks } : valid
    reports.value = [merged, ...reports.value.filter(r => r.id !== valid.id)]
      .sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt))
    backendIds.add(valid.id)
    persistReports()
    return merged
  }
  async function initialize() {
    if (ready.value) return
    if (initialization) return initialization
    initializing.value = true
    initialization = (async () => {
      try {
        const health = await establishSession()
        generationReady.value = health.ready
        const data = await listReports()
        const remote = data.reports.map(requireReport)
        backendIds.clear(); remote.forEach(r => backendIds.add(r.id))
        // Keep only distinguishable historical local documents; never generate a demo fallback.
        const legacy = reports.value.filter(r => !backendIds.has(r.id) && r.source !== 'generated')
        reports.value = [...remote, ...legacy].sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt))
        persistReports(); ready.value = true; error.value = ''
        if (pending.value) void resume()
        else if (lastContext?.taskId) {
          try { lastTask.value = await getTask(lastContext.taskId) }
          catch { lastContext = null; try { localStorage.removeItem(RECENT_TASK_KEY) } catch { /* Optional history cache. */ } }
        }
      } catch (cause) { error.value = cause instanceof Error ? cause.message : '服务连接失败，请重试。' }
      finally { initializing.value = false; initialization = undefined }
    })()
    return initialization
  }
  async function accept(task: ResearchTask, key: string) {
    if (!task || !['queued', 'running', 'paused', 'succeeded', 'failed', 'cancelled'].includes(task.status) || !Number.isInteger(task.step)) {
      throw new ApiError(502, 'INVALID_TASK', '任务响应无效，已保留恢复记录。')
    }
    return withPendingLock(() => {
      const current = syncPending()
      // Another tab may have completed this task and started a different one.
      if (current?.key !== key || current.taskId !== task.id) return true
      lastTask.value = task
      lastContext = { ...current }
      try { localStorage.setItem(RECENT_TASK_KEY, JSON.stringify(lastContext)) } catch { /* Pending recovery already has its durable record. */ }
      step.value = Math.max(0, Math.min(3, task.step))
      message.value = task.message
      if (task.status === 'paused') return true
      if (task.status === 'succeeded') {
        const report = requireReport(task.report)
        upsert(report, true)
        completedReportId.value = report.id
        persistPending(null)
        return true
      }
      if (task.status === 'failed' || task.status === 'cancelled') {
        if (task.status === 'failed') error.value = task.error?.message || '任务执行失败，请检查问题后重新发起。'
        message.value = task.status === 'cancelled' ? '任务已取消' : task.message
        persistPending(null)
        return true
      }
      return false
    })
  }
  async function resume() {
    if (!ready.value) { await initialize(); if (!ready.value) return }
    if (execution) return execution
    const p = syncPending()
    if (!p) return
    execution = (async () => {
      error.value = ''
      try {
        let task: ResearchTask
        if (!p.taskId) {
          if (p.replayBlocked || typeof p.createdAt !== 'string' || !Number.isFinite(Date.parse(p.createdAt)) || Date.now() - Date.parse(p.createdAt) > 24 * 60 * 60 * 1000 || Date.parse(p.createdAt) > Date.now() + 60000) {
            throw new ApiError(409, 'LOCAL_RECOVERY_BLOCKED', '提交结果无法安全恢复，未重新发送。请先核对报告列表，再清除本地等待记录。')
          }
          // A lost POST response is retried with this stored key and unchanged body.
          task = await createTask(p.request, p.key)
          if (!task || typeof task.id !== 'string' || !task.id) throw new ApiError(502, 'INVALID_TASK', '任务响应缺少 ID，已保留原请求。')
          if (!await updatePending(p.key, { taskId: task.id })) return
        } else task = await getTask(p.taskId)
        while (pending.value?.key === p.key) {
          if (await accept(task, p.key)) return
          if (pending.value.cancelRequested) {
            task = await cancelTask(task.id)
            if (await accept(task, p.key)) return
          }
          await sleep(1200)
          if (syncPending()?.key !== p.key) return
          task = await getTask(pending.value.taskId!)
        }
      } catch (cause) {
        if (syncPending()?.key !== p.key) return
        error.value = cause instanceof Error ? cause.message : '任务连接失败，请重试连接。'
        if (cause instanceof ApiError && cause.status === 409 && pending.value) {
          await updatePending(p.key, { replayBlocked: true })
        } else if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500 && cause.status !== 429) {
          if (cause.status === 404) error.value = '任务已不存在或当前会话无权访问。未重新提交，请核对后主动发起新任务。'
          await updatePending(p.key, null)
        }
      } finally {
        execution = undefined
        if (pending.value && pending.value.key !== p.key) queueMicrotask(() => { void resume() })
      }
    })()
    return execution
  }
  async function generate(request: ResearchRequest) {
    if (running.value || !request.question.trim()) return
    await initialize()
    if (!ready.value) return
    if (!generationReady.value) { error.value = '服务端尚未配置可用模型，暂不能生成；仍可导入和阅读文档。'; return }
    try {
      error.value = ''; step.value = 0; message.value = '正在提交任务'
      await withPendingLock(() => {
        // In-memory state may be stale when two already-open tabs submit together.
        if (syncPending()) return
        persistPending({ key: crypto.randomUUID(), request: { ...request, question: request.question.trim() }, taskId: null, cancelRequested: false, createdAt: new Date().toISOString() })
      })
      await resume()
    } catch (cause) { error.value = cause instanceof Error ? cause.message : '任务提交失败。' }
  }
  async function controlTask(action: 'pause' | 'resume' | 'retry', fromStage?: string) {
    const context = pending.value ?? lastContext
    if (!context?.taskId || actionBusy.value) return
    actionBusy.value = true; error.value = ''
    try {
      const task = await taskAction(context.taskId, action, fromStage)
      await withPendingLock(() => {
        const current = syncPending()
        if (current && current.key !== context.key) throw new Error('另一个调研正在运行，未替换当前任务。')
        if (!current) persistPending({ ...context, taskId: task.id, cancelRequested: false })
      })
      await accept(task, context.key)
      if (task.status === 'running' || task.status === 'queued') {
        // An old poller can be finishing after pause. Wait for it before resuming.
        if (execution) void execution.finally(() => { if (pending.value) void resume() })
        else void resume()
      }
    } catch (cause) { error.value = cause instanceof Error ? cause.message : '任务操作失败。' }
    finally { actionBusy.value = false }
  }
  async function cancel() {
    const p = syncPending()
    if (!p) return
    try {
      if (!await updatePending(p.key, { cancelRequested: true })) return
      message.value = '正在取消，请等待服务端确认'
      const current = pending.value
      if (current?.key === p.key && current.taskId) {
        const task = await cancelTask(current.taskId)
        // The existing poller consumes completion; never publish from two racing consumers.
        if (!execution) { if (!await accept(task, p.key)) await resume() }
      } else if (!execution) await resume()
    } catch (cause) { error.value = cause instanceof Error ? cause.message : '取消请求未确认，请重试连接。' }
  }
  async function dismissPending() {
    const current = syncPending()
    if (current?.replayBlocked) {
      try { if (await updatePending(current.key, null)) { error.value = ''; message.value = '' } }
      catch (cause) { error.value = cause instanceof Error ? cause.message : '无法清除本地记录。' }
    }
  }
  async function reconnect() { if (!ready.value) await initialize(); else if (pending.value) await resume(); else { ready.value = false; await initialize() } }
  async function loadReport(id: string) {
    await initialize()
    const local = reports.value.find(r => r.id === id)
    if (local && !backendIds.has(id) && local.source !== 'generated') return local
    if (!ready.value) throw new Error(error.value || '无法连接报告服务。')
    return upsert(await getReport(id))
  }
  async function addReport(report: Report) {
    await initialize()
    if (!ready.value) throw new Error(error.value || '无法同步导入文档。')
    return upsert(await importReport(report))
  }
  async function toggleBookmark(reportId: string, chapterId: string) {
    const report = reports.value.find(r => r.id === reportId)
    if (!report || bookmarkBusy.value.includes(reportId)) return
    const bookmarks = report.bookmarks.includes(chapterId) ? report.bookmarks.filter(id => id !== chapterId) : [...report.bookmarks, chapterId]
    bookmarkBusy.value.push(reportId)
    try {
      if (backendIds.has(reportId)) upsert(await saveBookmarks(reportId, bookmarks))
      else { report.bookmarks = bookmarks; persistReports() }
    } catch (cause) { error.value = cause instanceof Error ? cause.message : '书签保存失败，请重试。' }
    finally { bookmarkBusy.value = bookmarkBusy.value.filter(id => id !== reportId) }
  }
  return { reports, running, cancelling, pendingBlocked, dismissPending, ready, generationReady, initializing, step, message, activeQuestion, error, storageWarning, completedReportId,
    bookmarkBusy, bookmarkCount, initialize, generate, cancel, reconnect, toggleBookmark, addReport, loadReport, lastTask, taskId, paused, actionBusy, controlTask, upsert }
})
