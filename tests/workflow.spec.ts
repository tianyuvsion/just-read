import { test, expect } from '@playwright/test'

async function start(page: import('@playwright/test').Page, question: string) {
  await page.goto('/')
  await page.getByLabel('调研问题', { exact: true }).fill(question)
  await expect(page.getByRole('button', { name: '开始调研' })).toBeEnabled()
  const created = page.waitForResponse(r => r.url().endsWith('/api/v1/research-tasks') && r.request().method() === 'POST')
  await page.getByRole('button', { name: '开始调研' }).click()
  return (await created).json()
}

test('真实任务 API 生成、书签持久化、阅读搜索与导出', async ({ page }) => {
  const task = await start(page, '企业知识库的技术选型')
  expect(task.id).toBeTruthy()
  await expect(page.getByRole('heading', { name: /企业知识库的技术选型/ })).toBeVisible({ timeout: 30000 })
  const reportId = page.url().split('/report/')[1]!
  const response = await page.request.get(`/api/v1/reports/${reportId}`)
  expect(response.ok()).toBe(true)
  const report = await response.json()
  await page.locator('.bookmark-button').first().click()
  await expect(page.locator('.bookmark-button').first()).toHaveAttribute('aria-pressed', 'true')
  await page.reload()
  await expect(page.locator('.bookmark-button').first()).toHaveAttribute('aria-pressed', 'true')
  const saved = await (await page.request.get(`/api/v1/reports/${reportId}`)).json()
  expect(saved.bookmarks).toContain(report.chapters[0].id)
  await page.getByLabel('报告内全文搜索').fill('不存在的关键词abcdef')
  await expect(page.getByRole('heading', { name: '未找到相关内容' })).toBeVisible()
  const downloadPromise = page.waitForEvent('download')
  await page.getByLabel('导出格式').selectOption('html')
  await page.getByRole('button', { name: '导出', exact: true }).click()
  expect((await downloadPromise).suggestedFilename()).toBe(`${report.title}.html`)
  await page.getByRole('link', { name: '返回报告', exact: true }).click()
  await page.getByLabel('搜索报告或书签').fill(report.title)
  await expect(page.locator('.report-card')).toHaveCount(1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('取消任务通知后端且刷新后不创建报告', async ({ page }) => {
  const task = await start(page, '需要取消的任务')
  const cancelled = page.waitForResponse(r => r.url().endsWith(`/research-tasks/${task.id}/cancel`) && r.request().method() === 'POST')
  await page.getByRole('button', { name: '取消生成', exact: true }).click()
  await cancelled
  await page.reload()
  await expect(page.getByRole('button', { name: '开始调研' })).toBeDisabled()
  await expect.poll(async () => (await (await page.request.get(`/api/v1/research-tasks/${task.id}`)).json()).status).toBe('cancelled')
  await expect(page.locator('.generation-panel')).toHaveCount(0)
  expect((await (await page.request.get('/api/v1/reports')).json()).reports).toHaveLength(0)
  await page.goto('/#/report/missing')
  await expect(page.getByRole('heading', { name: '这份报告不在当前工作空间' })).toBeVisible()
})

test('POST 丢响应后刷新复用幂等键，只生成一份报告', async ({ page }) => {
  const keys: string[] = []
  let first = true
  await page.route('**/api/v1/research-tasks', async route => {
    if (route.request().method() !== 'POST') return route.continue()
    keys.push(route.request().headers()['idempotency-key']!)
    if (first) { first = false; await route.fetch(); await route.abort('failed') }
    else await route.continue()
  })
  await page.goto('/')
  await page.getByLabel('调研问题', { exact: true }).fill('丢失响应恢复测试')
  await expect(page.getByRole('button', { name: '开始调研' })).toBeEnabled()
  await page.getByRole('button', { name: '开始调研' }).click()
  await expect(page.getByRole('alert')).toContainText('无法连接服务')
  const pending = await page.evaluate(() => JSON.parse(localStorage.getItem('just-read:pending:v1')!))
  expect(pending.key).toBe(keys[0]); expect(pending.taskId).toBeNull()
  await page.reload()
  await expect(page.getByRole('heading', { name: /丢失响应恢复测试/ })).toBeVisible({ timeout: 30000 })
  expect(keys).toHaveLength(2); expect(keys[1]).toBe(keys[0])
  expect((await (await page.request.get('/api/v1/reports')).json()).reports).toHaveLength(1)
  expect(await page.evaluate(() => localStorage.getItem('just-read:pending:v1'))).toBeNull()
})

test('已知任务 ID 刷新仅 GET 恢复，不再次 POST', async ({ page }) => {
  let posts = 0
  page.on('request', r => { if (r.url().endsWith('/api/v1/research-tasks') && r.method() === 'POST') posts++ })
  const task = await start(page, '已知任务刷新恢复')
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('just-read:pending:v1') ?? 'null')?.taskId)).toBe(task.id)
  await page.reload()
  await expect(page.getByRole('heading', { name: /已知任务刷新恢复/ })).toBeVisible({ timeout: 30000 })
  expect(posts).toBe(1)
})

test('未知任务 ID 不自动重新提交', async ({ page }) => {
  let posts = 0
  page.on('request', r => { if (r.url().endsWith('/api/v1/research-tasks') && r.method() === 'POST') posts++ })
  await page.addInitScript(() => localStorage.setItem('just-read:pending:v1', JSON.stringify({
    key: 'unknown-task-test', request: { question: '不能偷偷重建', depth: 'deep' }, taskId: 'missing-task-id', cancelRequested: false,
  })))
  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('未重新提交')
  expect(posts).toBe(0)
  expect((await (await page.request.get('/api/v1/reports')).json()).reports).toHaveLength(0)
})

test('报告 API 读取与真实图表单位，不裁成虚构百分比', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByLabel('上传文档', { exact: true })).toBeEnabled()
  const response = await page.request.post('/api/v1/reports/import', { data: {
    id: 'client-id', source: 'import', title: '实际单位图表', question: '', category: '导入文档', summary: '图表展示测试',
    createdAt: new Date().toISOString(), minutes: 1, chapters: [{ id: 'data', title: '实验数据', paragraphs: ['数值超过100也应完整显示。'] }],
    chart: [{ label: '测量A', value: 250 }, { label: '测量B', value: 500 }],
    chartMeta: { title: '响应时延', unit: 'ms', note: '测试数据，仅验证渲染' }, bookmarks: [],
  } })
  expect(response.ok()).toBe(true)
  const report = await response.json()
  await page.goto(`/#/report/${report.id}`)
  await expect(page.getByText('响应时延', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '测量B：500 ms' })).toBeVisible()
  await page.getByRole('button', { name: '数据', exact: true }).click()
  await expect(page.getByRole('columnheader', { name: 'ms', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: '500', exact: true })).toBeVisible()
})

test('服务不可用时不自动退回演示数据', async ({ page }) => {
  await page.route('**/api/v1/reports', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'UNAVAILABLE', message: '测试服务不可用' } }) }))
  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('测试服务不可用')
  await expect(page.locator('.report-card')).toHaveCount(0)
  await page.getByLabel('调研问题', { exact: true }).fill('不能生成演示替代品')
  await expect(page.getByRole('button', { name: '开始调研' })).toBeDisabled()
})

test('保留已存在的本地演示报告并明确标注', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('just-read:reports:v1', JSON.stringify([{
    id: 'legacy-demo', title: '旧版演示', question: '旧版演示', category: '演示', summary: '历史本地记录', createdAt: '2026-10-01T00:00:00Z',
    minutes: 1, chapters: [{ id: 'overview', title: '演示内容', paragraphs: ['历史演示数据'] }], chart: [{ label: '示例', value: 82 }], bookmarks: [],
  }])))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '旧版演示' })).toBeVisible()
  await page.getByRole('link', { name: /旧版演示/ }).click()
  await expect(page.locator('.report-disclaimer')).toContainText('演示报告')
  await expect(page.getByText('历史演示评分', { exact: true })).toBeVisible()
})

test('超期和缺少时间的未知提交记录不会自动重 POST', async ({ page }) => {
  let posts = 0
  page.on('request', r => { if (r.url().endsWith('/api/v1/research-tasks') && r.method() === 'POST') posts++ })
  await page.goto('/')
  await expect(page.getByLabel('上传文档', { exact: true })).toBeEnabled()
  for (const createdAt of [new Date(Date.now() - 25 * 3600_000).toISOString(), null]) {
    await page.evaluate(stamp => localStorage.setItem('just-read:pending:v1', JSON.stringify({
      key: crypto.randomUUID(), request: { question: '过期请求不能自动重建', depth: 'deep' }, taskId: null,
      cancelRequested: false, ...(stamp ? { createdAt: stamp } : {}),
    })), createdAt)
    await page.reload()
    await expect(page.getByRole('alert')).toContainText('未重新发送')
    await expect(page.getByRole('button', { name: '清除本地等待记录' })).toBeVisible()
    await page.getByRole('button', { name: '清除本地等待记录' }).click()
    await expect.poll(() => page.evaluate(() => localStorage.getItem('just-read:pending:v1'))).toBeNull()
  }
  expect(posts).toBe(0)
})

for (const withoutLocks of [false, true]) {
  test(`双标签页共享任务，不覆盖待恢复记录${withoutLocks ? '（无 Web Locks 回退）' : ''}`, async ({ page, context }) => {
    const second = await context.newPage()
    // Delay delivery to reproduce a second tab whose in-memory state is stale.
    await second.addInitScript(noLocks => {
      if (noLocks) Object.defineProperty(navigator, 'locks', { value: undefined })
      window.addEventListener('storage', event => {
        if (event.key === 'just-read:pending:v1' && !JSON.parse(event.newValue ?? 'null')?.taskId) event.stopImmediatePropagation()
      })
    }, withoutLocks)
    const keys: string[] = []
    const statuses: number[] = []
    let release!: () => void
    const gate = new Promise<void>(resolve => { release = resolve })
    await context.route('**/api/v1/research-tasks', async route => {
      if (route.request().method() !== 'POST') return route.continue()
      keys.push(route.request().headers()['idempotency-key']!)
      await gate
      await route.continue()
    })
    context.on('response', response => {
      if (response.url().endsWith('/api/v1/research-tasks') && response.request().method() === 'POST') statuses.push(response.status())
    })
    try {
      await page.goto('/')
      await expect(page.getByLabel('上传文档', { exact: true })).toBeEnabled()
      await second.goto('/')
      await expect(second.getByLabel('上传文档', { exact: true })).toBeEnabled()
      await page.getByLabel('调研问题', { exact: true }).fill('第一页的唯一任务')
      await second.getByLabel('调研问题', { exact: true }).fill('第二页不能覆盖任务')
      await page.getByRole('button', { name: '开始调研' }).click()
      await expect.poll(() => keys.length).toBe(1)
      const firstPending = await page.evaluate(() => JSON.parse(localStorage.getItem('just-read:pending:v1')!))
      await expect(second.getByRole('button', { name: '开始调研' })).toBeEnabled()
      await second.getByRole('button', { name: '开始调研' }).click()
      await expect(second.locator('.generation-panel')).toContainText('第一页的唯一任务')
      expect(await second.evaluate(() => JSON.parse(localStorage.getItem('just-read:pending:v1')!).key)).toBe(firstPending.key)
      expect(new Set(keys)).toEqual(new Set([firstPending.key]))
      release()
      await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('just-read:pending:v1') ?? 'null')?.taskId)).toBeTruthy()
      const taskId = await page.evaluate(() => JSON.parse(localStorage.getItem('just-read:pending:v1')!).taskId)
      await page.reload()
      await expect(page.locator('.generation-panel')).toContainText('第一页的唯一任务')
      await page.getByRole('button', { name: '取消生成', exact: true }).click()
      await expect.poll(async () => (await (await page.request.get(`/api/v1/research-tasks/${taskId}`)).json()).status).toBe('cancelled')
      await expect(second.locator('.generation-panel')).toHaveCount(0)
      expect(new Set(keys)).toEqual(new Set([firstPending.key]))
      expect(statuses).not.toContain(409)
      expect(await page.evaluate(() => localStorage.getItem('just-read:pending:v1'))).toBeNull()
    } finally { release(); await second.close() }
  })
}

test('旧取消响应延迟返回不会清除新任务恢复记录', async ({ page }) => {
  let oldId = ''
  let oldPollStarted!: () => void
  const oldPoll = new Promise<void>(resolve => { oldPollStarted = resolve })
  let allowOldPoll!: () => void
  const cancelledOnServer = new Promise<void>(resolve => { allowOldPoll = resolve })
  let releaseCancel!: () => void
  const delayedCancel = new Promise<void>(resolve => { releaseCancel = resolve })
  let newId = ''
  await page.route('**/api/v1/research-tasks/*', async route => {
    if (route.request().method() !== 'GET') return route.continue()
    if (oldId && route.request().url().endsWith(`/${oldId}`)) {
      oldPollStarted()
      await cancelledOnServer
      return route.continue()
    }
    if (newId && route.request().url().endsWith(`/${newId}`)) return route.abort('failed')
    return route.continue()
  })
  await page.route('**/api/v1/research-tasks/*/cancel', async route => {
    const response = await route.fetch()
    allowOldPoll()
    await delayedCancel
    await route.fulfill({ response })
  })
  try {
    oldId = (await start(page, '旧任务等待取消响应')).id
    await oldPoll
    await page.getByRole('button', { name: '取消生成', exact: true }).click()
    await expect(page.locator('.generation-panel')).toHaveCount(0)
    await page.getByLabel('调研问题', { exact: true }).fill('新任务恢复记录不能被旧响应删除')
    const created = page.waitForResponse(r => r.url().endsWith('/api/v1/research-tasks') && r.request().method() === 'POST')
    await page.getByRole('button', { name: '开始调研' }).click()
    newId = (await (await created).json()).id
    await expect(page.getByRole('alert')).toContainText('无法连接服务')
    const newPending = await page.evaluate(() => JSON.parse(localStorage.getItem('just-read:pending:v1')!))
    expect(newPending.taskId).toBe(newId)
    const oldResponse = page.waitForResponse(r => r.url().endsWith(`/${oldId}/cancel`))
    releaseCancel()
    await (await oldResponse).finished()
    // Let fetch consumption and the following Web Lock callback finish.
    await page.waitForTimeout(100)
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('just-read:pending:v1')!).key)).toBe(newPending.key)
    await expect(page.locator('.generation-panel')).toContainText('新任务恢复记录不能被旧响应删除')
  } finally { allowOldPoll(); releaseCancel() }
})
