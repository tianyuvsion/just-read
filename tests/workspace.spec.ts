import { test, expect, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import type { Report, ResearchTask, VisualSpec } from '../src/types'

function simulatedReport(): Report {
  const data = { labels: ['模拟 A', '模拟 B'], series: [{ name: '模拟系列', values: [-2, 4] }], unit: '模拟单位', provenance: { kind: 'test', note: '明确模拟内容，不代表真实研究' } }
  const graph = { nodes: [{ id: 'input', label: '模拟输入' }, { id: 'output', label: '模拟输出' }], edges: [{ source: 'input', target: 'output', type: 'supports', label: '模拟连接', evidence_refs: ['ev-1'] }] }
  return {
    id: 'simulated-ad', source: 'import', title: '模拟 A–D 工作区报告', question: '模拟问题', category: '回归测试', summary: '仅验证本地功能，所有材料、结论和数值均为模拟。',
    createdAt: new Date().toISOString(), minutes: 1, chapters: [{ id: 'overview', title: '模拟结论', paragraphs: ['这是用于工作区验收的模拟正文。'] }], chart: [], bookmarks: [], version: 1,
    workflow: {
      schema_version: '1.0', research_spec: { question: '模拟问题', depth: 'brief', reader: '测试读者', scope: '测试范围', source_ids: [], enable_3d: true },
      sources: [{ id: 'source-1', title: '模拟材料', kind: 'upload', chunks: [{ id: 'chunk-1', text: '模拟原文摘录，用于定位测试。', page: 2, paragraph: 1 }] }],
      evidence: [{ id: 'ev-1', source_id: 'source-1', chunk_id: 'chunk-1', quote: '模拟原文摘录', locator: { page: 2, paragraph: 1 }, verification: { quote_match: true }, statement_kind: 'source_quote' }],
      plan: { subquestions: [{ id: 'q1', question: '模拟子问题', query: '模拟检索查询' }] },
      research_ir: { question_answers: [{ question: '模拟子问题', answer: '模拟答案' }], claims: [{ id: 'claim-1', text: '模拟结论', evidence_refs: ['ev-1'] }], unknowns: ['测试未知项'] },
      datasets: [{ id: 'data-1', metric: '模拟差值', unit: '模拟单位', rows: [{ label: '模拟 A', value: -2 }, { label: '模拟 B', value: 4 }] }],
      calculations: [{ id: 'calc-1', operation: 'difference', inputs: [-2, 4], formula: '4 - (-2)', result: 6, unit: '模拟单位', source_refs: ['ev-1'], status: 'computed' }],
      reading_plan: { audience: '测试读者', goal: '理解模拟过程', chapter_order: ['overview'], sections: [{ chapter_id: 'overview', question: '模拟子问题', goal: '理解模拟关系', evidence_refs: ['ev-1'], visual_ids: ['flow-1'] }] },
      narrative_blocks: [{ id: 'block-1', chapter_id: 'overview', semantic_role: 'conclusion', text: '模拟语义块', claim_refs: ['claim-1'], evidence_refs: ['ev-1'] }],
      visuals: (['flow', 'relation', 'bar', 'line', 'table'] as const).map(kind => ({ id: `${kind}-1`, kind, title: `模拟 ${kind} 图解`, chapter_id: 'overview', ...data, ...graph } as VisualSpec)),
      scene: { id: 'scene-1', title: '模拟概念结构', kind: 'schematic', parts: [
        { id: 'part-1', label: '模拟壳体', geometry: 'box', position: [-1, 0, 0], size: [1, 1, 1], color: '#1677ff', description: '模拟立方体部件', dimensions_known: false, evidence_refs: ['ev-1'] },
        { id: 'part-2', label: '模拟核心', geometry: 'sphere', position: [1, 0, 0], size: [1, 1, 1], color: '#13a8a8', dimensions_known: false },
      ], relations: [{ source: 'part-1', target: 'part-2', type: 'contains' }], unknowns: ['真实尺寸未知，全部为概念示意'] },
    },
  }
}

async function ready(page: Page) {
  await page.goto('/')
  await expect(page.getByLabel('上传文档', { exact: true })).toBeEnabled()
}

test('模拟材料经真实上传 API 解析并选入研究请求，保留原文定位', async ({ page }) => {
  await ready(page)
  await page.getByLabel('添加研究材料', { exact: true }).setInputFiles({ name: '模拟研究材料.md', mimeType: 'text/markdown', buffer: Buffer.from('# 模拟研究材料\n\n模拟量 A 为 12，模拟量 B 为 18。全部数据仅用于软件测试。') })
  await expect(page.getByLabel('使用材料：模拟研究材料.md', { exact: true })).toBeChecked()
  await expect(page.locator('.source-preview')).toContainText('模拟量 A 为 12')
  await page.getByLabel('调研问题', { exact: true }).fill('基于上传的模拟材料解释两项模拟量的差值')
  await page.getByLabel('目标读者').fill('测试读者')
  await page.getByLabel('研究范围').fill('只研究模拟材料中的数值')
  await page.getByLabel('研究深度').selectOption('brief')
  await page.getByLabel('启用概念三维').check()
  const created = page.waitForResponse(r => r.url().endsWith('/api/v1/research-tasks') && r.request().method() === 'POST')
  await page.getByRole('button', { name: '开始调研' }).click()
  const response = await created
  expect(response.ok()).toBe(true)
  const body = response.request().postDataJSON()
  expect(body.source_ids).toHaveLength(1)
  expect(body.reader).toBe('测试读者'); expect(body.scope).toBe('只研究模拟材料中的数值'); expect(body.enable_3d).toBe(true)
  await expect(page.locator('.report-workspace')).toBeVisible({ timeout: 35000 })
  await page.locator('.report-workspace > summary').click()
  await expect(page.getByRole('tabpanel', { name: 'A 目标与材料' })).toContainText('模拟研究材料')
})

test('模拟完整工作区经真实报告 API 完成证据、五类图解、三维编辑、版本、人工审查与冻结分享', async ({ page, browser }, testInfo) => {
  test.setTimeout(90000)
  await ready(page)
  const sourceResponse = await page.request.post('/api/v1/sources', { data: {
    name: '模拟私有原件.md', media_type: 'text/markdown',
    content_base64: Buffer.from('模拟原文摘录，用于定位测试。\n\n模拟私有未引用内容，不应由分享页的来源查看器展示。').toString('base64'),
  } })
  expect(sourceResponse.ok()).toBe(true)
  const privateSource = await sourceResponse.json()
  const original = simulatedReport()
  original.workflow!.sources = [{ id: privateSource.id, title: '模拟材料', kind: 'upload', chunks: privateSource.chunks.map((chunk: { id: string; page?: number; paragraph?: number }) => ({ id: chunk.id, page: chunk.page, paragraph: chunk.paragraph, text: '' })) }]
  original.workflow!.evidence![0]!.source_id = privateSource.id
  original.workflow!.evidence![0]!.chunk_id = privateSource.chunks[0].id
  const response = await page.request.post('/api/v1/reports/import', { data: original })
  expect(response.ok()).toBe(true)
  const saved: Report = await response.json()
  await page.goto(`/#/report/${saved.id}`)
  await page.locator('.report-workspace > summary').click()
  const workspace = page.locator('.report-workspace')
  await workspace.getByRole('button', { name: '定位原文', exact: true }).click()
  await expect(workspace.locator('.record-preview')).toContainText('模拟原文摘录，用于定位测试。')
  await workspace.getByRole('tab', { name: 'B · 研究与计算', exact: true }).click()
  await expect(workspace).toContainText('4 - (-2)')
  await workspace.getByRole('tab', { name: 'C · 阅读与图解', exact: true }).click()
  for (const kind of ['flow', 'relation', 'bar', 'line', 'table']) await expect(workspace.locator(`[data-visual-kind="${kind}"]`)).toBeVisible()
  const canvas = workspace.getByLabel('可旋转三维结构')
  await canvas.focus()
  const before = await canvas.getAttribute('data-yaw')
  await canvas.press('ArrowRight')
  await expect(canvas).not.toHaveAttribute('data-yaw', before!)
  await workspace.getByLabel('爆炸视图', { exact: true }).focus()
  await workspace.getByLabel('爆炸视图', { exact: true }).press('ArrowRight')
  await expect(workspace.getByLabel('爆炸视图', { exact: true })).toHaveValue('0.05')
  await workspace.getByRole('button', { name: '模拟壳体', exact: true }).click()
  await workspace.getByLabel('部件名称', { exact: true }).fill('模拟修改部件')
  await expect(workspace.getByRole('button', { name: '模拟修改部件', exact: true })).toBeVisible()
  await workspace.getByLabel('导入三维 JSON 包', { exact: true }).setInputFiles({ name: 'simulated.scene.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({ id: 'imported-scene', title: '模拟导入三维包', kind: 'schematic', parts: [{ id: 'cylinder', label: '模拟圆柱', geometry: 'cylinder', position: [0, 0, 0], size: [1, 2, 1], color: '#722ed1', dimensions_known: false }] })) })
  await expect(workspace.getByRole('button', { name: '模拟圆柱', exact: true })).toBeVisible()
  await workspace.locator('.scene-viewer').screenshot({ path: testInfo.outputPath('scene.png') })
  await workspace.getByRole('button', { name: '保存新版本', exact: true }).click()
  await expect(workspace).toContainText('已保存为 v2')
  await workspace.getByRole('tab', { name: 'D · 审查与交付', exact: true }).click()
  const aiChecked = page.waitForResponse(r => r.url().endsWith(`/reports/${saved.id}/actions`) && r.request().postDataJSON()?.action === 'review')
  await workspace.getByRole('button', { name: 'AI 审查当前版本', exact: true }).click()
  expect((await (await aiChecked).json()).simulated).toBe(true)
  await expect(workspace).toContainText('检查已完成')
  await workspace.getByText('提交人工审查', { exact: true }).click()
  await workspace.getByLabel('审查人', { exact: true }).fill('模拟审查员')
  await workspace.getByLabel('人工审查说明').fill('仅确认软件交互，不代表事实核验。')
  await workspace.getByRole('button', { name: '保存人工审查', exact: true }).click()
  await expect(workspace).toContainText('模拟审查员')
  await workspace.getByText('编辑报告，保存为新版本', { exact: true }).click()
  await workspace.getByLabel('编辑报告标题').fill('模拟标题新版本')
  await workspace.getByRole('tabpanel').getByRole('button', { name: '保存新版本', exact: true }).click()
  await expect(workspace).toContainText('已保存为 v3')
  await workspace.getByLabel('历史版本').selectOption('1')
  await workspace.getByRole('button', { name: '与当前版本比较', exact: true }).click()
  await expect(workspace).toContainText('标题：模拟 A–D 工作区报告 → 模拟标题新版本')
  await workspace.getByRole('button', { name: '创建冻结版本分享', exact: true }).click()
  const url = await workspace.getByLabel('分享链接', { exact: true }).inputValue()
  const guest = await browser.newContext()
  const guestPage = await guest.newPage()
  let guestPrivateSourceCalls = 0
  guestPage.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/v1/sources/')) guestPrivateSourceCalls++ })
  await guestPage.goto(url)
  await expect(guestPage.getByRole('heading', { name: '模拟 A–D 工作区报告', exact: true })).toBeVisible()
  await expect(guestPage.locator('.bookmark-button')).toHaveCount(0)
  await guestPage.locator('.report-workspace > summary').click()
  await guestPage.getByRole('button', { name: '定位原文', exact: true }).click()
  await expect(guestPage.locator('.record-preview')).toContainText('模拟原文摘录')
  await expect(guestPage.locator('.record-preview')).not.toContainText('模拟私有未引用内容')
  expect(guestPrivateSourceCalls).toBe(0)
  await workspace.getByRole('button', { name: '恢复为新版本', exact: true }).click()
  await expect(workspace).toContainText('已从历史版本创建新版本')
  const downloading = page.waitForEvent('download')
  await workspace.getByRole('button', { name: '下载自包含 ZIP', exact: true }).click()
  const zip = await readFile((await (await downloading).path())!)
  expect(zip.subarray(0, 2).toString()).toBe('PK')
  await workspace.getByRole('button', { name: '撤销此分享链接', exact: true }).click()
  await guestPage.reload()
  await expect(guestPage.getByRole('heading', { name: '这份报告不在当前工作空间' })).toBeVisible()
  await guest.close()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('明确模拟任务 API：暂停后刷新继续，仅轮询已有任务；失败重试接收新任务 ID', async ({ page }) => {
  let state: ResearchTask['status'] = 'running', currentId = 'simulated-task-1', posts = 0, resumeCount = 0
  const result = simulatedReport(); result.id = 'mock-result'
  function task(): ResearchTask { return { id: currentId, status: state, step: 1, message: `模拟状态 ${state}`, report: state === 'succeeded' ? result : null, error: state === 'failed' ? { code: 'SIMULATED', message: '模拟可重试错误' } : null, expiresAt: null } }
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname, method = route.request().method()
    let value: unknown
    if (path.endsWith('/health')) value = { ready: true, mode: 'test' }
    else if (path.endsWith('/sources')) value = { sources: [] }
    else if (path.endsWith('/reports')) value = { reports: [] }
    else if (path.endsWith('/versions')) value = { versions: [] }
    else if (path.endsWith('/reports/mock-result')) value = result
    else if (path.endsWith('/research-tasks') && method === 'POST') { posts++; value = task() }
    else if (path.endsWith('/runs')) value = { runs: [{ id: 'run-1', kind: 'stage', stage: 'B', status: state, message: '明确模拟记录' }] }
    else if (path.endsWith('/actions')) {
      const body = route.request().postDataJSON()
      if (body.action === 'pause') state = 'paused'
      if (body.action === 'resume') { resumeCount++; state = 'failed' }
      if (body.action === 'retry') { expect(body.from_stage).toBe('B'); currentId = 'simulated-task-2'; state = 'succeeded' }
      value = task()
    } else if (path.includes('/research-tasks/')) value = task()
    else return route.fulfill({ status: 404, json: { error: { code: 'MOCK_NOT_FOUND', message: '模拟接口不存在' } } })
    await route.fulfill({ json: value })
  })
  await ready(page)
  await page.getByLabel('调研问题', { exact: true }).fill('模拟暂停恢复')
  await page.getByRole('button', { name: '开始调研' }).click()
  await page.getByRole('button', { name: '暂停任务', exact: true }).click()
  await expect(page.getByRole('button', { name: '继续任务', exact: true })).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: '继续任务', exact: true }).click()
  await expect(page.getByRole('button', { name: '重试任务', exact: true })).toBeVisible()
  await page.getByLabel('重试起点').selectOption('B')
  await page.getByRole('button', { name: '重试任务', exact: true }).click()
  await expect(page.getByRole('heading', { name: result.title, exact: true })).toBeVisible()
  expect(posts).toBe(1); expect(resumeCount).toBe(1)
  expect(await page.evaluate(() => localStorage.getItem('just-read:pending:v1'))).toBeNull()
})

test('模拟数据经真实核算 API 按选择顺序复算并生成新版本，旧 AI 审查不冒充当前审查', async ({ page }) => {
  await ready(page)
  const report = simulatedReport()
  report.workflow!.datasets![0]!.rows![0]!.id = 'row-explicit-a'
  report.workflow!.ai_quality_review = { kind: 'ai', report_version: 1, status: 'reviewed', summary: '模拟旧版本审查，未调用真实模型。' }
  const imported = await page.request.post('/api/v1/reports/import', { data: report })
  expect(imported.ok()).toBe(true)
  const saved: Report = await imported.json()
  await page.goto(`/#/report/${saved.id}`)
  await page.locator('.report-workspace > summary').click()
  const workspace = page.locator('.report-workspace')
  await workspace.getByRole('tab', { name: 'D · 审查与交付', exact: true }).click()
  await expect(workspace.locator('.ai-review-version')).toContainText('对应报告 v1')
  await workspace.getByRole('tab', { name: 'B · 研究与计算', exact: true }).click()
  await workspace.getByLabel('核算运算', { exact: true }).selectOption('difference')
  await expect(workspace.locator('.calculation-hint')).toContainText('第二项 − 第一项')
  const calculate = workspace.getByRole('button', { name: '计算并保存新版本', exact: true })
  await expect(calculate).toBeDisabled()
  await workspace.getByLabel('核算输入：模拟差值 · 模拟 B', { exact: true }).check()
  await expect(calculate).toBeDisabled()
  await workspace.getByLabel('核算输入：模拟差值 · 模拟 A', { exact: true }).check()
  await expect(workspace.getByLabel('核算输入顺序').locator('li').first()).toContainText('模拟 B：4')
  const computed = page.waitForResponse(r => r.url().endsWith(`/reports/${saved.id}/actions`) && r.request().postDataJSON()?.action === 'calculate')
  await calculate.click()
  const response = await computed
  expect(response.ok()).toBe(true)
  expect(response.request().postDataJSON()).toEqual({ action: 'calculate', base_version: 1, operation: 'difference', input_refs: ['data-1:1', 'row-explicit-a'] })
  const value = await response.json()
  expect(value.calculation.result).toBe(-6)
  expect(value.report.version).toBe(2)
  await expect(workspace).toContainText('核算完成，已保存为 v2')
  await expect(workspace).toContainText('结果：-6')
  await workspace.getByRole('tab', { name: 'D · 审查与交付', exact: true }).click()
  await expect(workspace.locator('.ai-review-version')).toContainText('v1 的历史 AI 审查')
  await expect(workspace.locator('.ai-review-version')).toContainText('当前 v2 尚需重新审查')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
