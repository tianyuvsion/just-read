import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import type { Report } from '../src/types'

test('模拟宽松报告经真实导入 API 显示负数单点及待核实说明，并完整导出 HTML', async ({ page }) => {
  const note = '待核实：本报告及负数均为浏览器回归的模拟内容，未经过来源核验。'
  const report: Report = {
    id: 'simulated-relaxed-report', source: 'import', title: '模拟宽松报告回归',
    question: '', category: '模拟测试', summary: '仅验证报告阅读和导出，不代表真实调研结果。',
    createdAt: new Date().toISOString(), minutes: 1,
    chapters: [
      { id: 'comparison', title: '模拟候选数据', paragraphs: ['负数单点示例用于验证数值和单位的展示。'] },
      { id: 'generation-notes', title: '生成说明', paragraphs: [note] },
    ],
    chart: [{ label: '模拟样本', value: -12.5 }],
    chartMeta: { title: '模拟变化量', unit: '个百分点', note: '候选数据，待核实；仅供模拟测试。' },
    bookmarks: [],
  }

  await page.goto('/')
  await expect(page.getByLabel('上传文档', { exact: true })).toBeEnabled()
  // Use the local backend and its session cookie; no intercepted API response.
  const response = await page.request.post('/api/v1/reports/import', { data: report })
  expect(response.status()).toBe(200)
  const saved: Report = await response.json()
  expect(saved.id).not.toBe(report.id)
  expect(saved.chart).toEqual(report.chart)

  await page.goto(`/#/report/${saved.id}`)
  await expect(page.getByRole('heading', { name: report.title, exact: true })).toBeVisible()
  const notes = page.locator('#chapter-generation-notes')
  await expect(notes.getByRole('heading', { name: /生成说明/ })).toBeVisible()
  await expect(notes).toContainText(note)
  await expect(page.locator('.bar-row')).toHaveCount(1)
  const bar = page.getByRole('button', { name: '模拟样本：-12.5 个百分点', exact: true })
  await expect(bar).toBeVisible()
  await bar.click()
  await expect(page.locator('.chart-selection')).toHaveText('模拟样本：-12.5 个百分点')
  await page.getByRole('button', { name: '数据', exact: true }).click()
  await expect(page.getByRole('columnheader', { name: '个百分点', exact: true })).toBeVisible()
  await expect(page.getByRole('cell', { name: '-12.5', exact: true })).toBeVisible()

  // Reading filters must not omit the generation notes from the exported report.
  await page.getByLabel('报告内全文搜索').fill('负数单点示例')
  await expect(notes).toHaveCount(0)
  await page.getByLabel('导出格式').selectOption('html')
  const downloading = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出', exact: true }).click()
  const download = await downloading
  expect(download.suggestedFilename()).toBe(`${report.title}.html`)
  expect(await download.failure()).toBeNull()
  const path = await download.path()
  expect(path).toBeTruthy()
  const html = await readFile(path!, 'utf8')
  expect(html).toContain('<section id="generation-notes"><h2>生成说明</h2>')
  expect(html).toContain(note)
  expect(html).toContain('<th>个百分点</th>')
  expect(html).toContain('<td>-12.5</td>')
})

test('模拟极大有限正负值经真实导入 API 后柱形范围不溢出', async ({ page }) => {
  const report: Report = {
    id: 'simulated-extreme-chart', source: 'import', title: '模拟极大数值图表回归',
    question: '', category: '模拟测试', summary: '仅用于数值边界测试，非实际研究数据。',
    createdAt: new Date().toISOString(), minutes: 1,
    chapters: [{ id: 'comparison', title: '模拟边界数据', paragraphs: ['两个极大有限数值仅验证图表布局。'] }],
    chart: [{ label: '模拟负值', value: -1e308 }, { label: '模拟正值', value: 1e308 }],
    chartMeta: { title: '模拟极值比较', unit: '模拟单位', note: '候选模拟数据，待核实；不用于研究结论。' },
    bookmarks: [],
  }

  await page.goto('/')
  await expect(page.getByLabel('上传文档', { exact: true })).toBeEnabled()
  const response = await page.request.post('/api/v1/reports/import', { data: report })
  expect(response.status()).toBe(200)
  const saved: Report = await response.json()
  expect(saved.chart).toEqual(report.chart)
  await page.goto(`/#/report/${saved.id}`)
  await expect(page.getByRole('heading', { name: report.title, exact: true })).toBeVisible()

  const fills = page.locator('.bar-fill')
  await expect(fills).toHaveCount(2)
  for (const [index, expectedLeft] of [0, 50].entries()) {
    const fill = fills.nth(index)
    await expect(fill).toBeVisible()
    const geometry = await fill.evaluate(element => {
      const rect = element.getBoundingClientRect()
      const track = element.parentElement!.getBoundingClientRect()
      return {
        style: element.getAttribute('style') ?? '',
        widthPercent: rect.width / track.width * 100,
        leftPercent: (rect.left - track.left) / track.width * 100,
      }
    })
    expect(geometry.style).not.toMatch(/Infinity|NaN/)
    expect(Number.isFinite(geometry.widthPercent)).toBe(true)
    expect(Number.isFinite(geometry.leftPercent)).toBe(true)
    expect(geometry.widthPercent).toBeCloseTo(50, 0)
    expect(geometry.leftPercent).toBeCloseTo(expectedLeft, 0)
  }
})
