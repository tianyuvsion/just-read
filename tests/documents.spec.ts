import { test, expect } from '@playwright/test'
import { Document, Packer, Paragraph } from 'docx'
import { jsPDF } from 'jspdf'
import { readFile } from 'node:fs/promises'
import mammoth from 'mammoth'

test('导入 Markdown、持久化、搜索和多格式导出完整正文', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('上传文档', { exact: true }).setInputFiles({
    name: '中文文档.md', mimeType: 'text/markdown',
    buffer: Buffer.from('# 总标题\n\n## 项目背景\n\n这是中文正文。<script>alert(1)</script>\n\n## 结论\n\n这段内容也需要导出。'),
  })
  await expect(page.getByRole('heading', { name: '中文文档', exact: true })).toBeVisible()
  await expect(page.locator('.report-chapter')).toHaveCount(3)
  await expect(page.locator('.report-disclaimer')).toContainText('本地导入文档')
  await page.reload()
  await page.getByLabel('报告内全文搜索').fill('中文正文')
  await expect(page.locator('.report-chapter')).toHaveCount(1)
  for (const format of ['md', 'docx', 'pdf', 'html']) {
    await page.getByLabel('导出格式').selectOption(format)
    const downloading = page.waitForEvent('download')
    await page.getByRole('button', { name: '导出', exact: true }).click()
    const download = await downloading
    expect(download.suggestedFilename()).toBe(`中文文档.${format}`)
    const data = await readFile((await download.path())!)
    if (format === 'md') {
      expect(data.toString()).toContain('这段内容也需要导出。')
      expect(data.toString()).toContain('# 中文文档')
    }
    if (format === 'docx') {
      const result = await mammoth.extractRawText({ buffer: data })
      expect(result.value).toContain('这是中文正文。')
      expect(result.value).toContain('这段内容也需要导出。')
    }
    if (format === 'pdf') {
      expect(data.subarray(0, 5).toString()).toBe('%PDF-')
      expect(data.length).toBeGreaterThan(1000)
    }
    if (format === 'html') {
      expect(data.toString()).toContain('&lt;script&gt;')
      expect(data.toString()).not.toContain('<script>')
      expect(data.toString()).toContain('本地导入文档')
    }
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('导入 Word 和 PDF 的实际正文', async ({ page }) => {
  const word = await Packer.toBuffer(new Document({ sections: [{ children: [new Paragraph('Word 中文正文测试')] }] }))
  const pdf = new jsPDF()
  pdf.text('PDF import verification', 20, 20)
  pdf.addPage()
  pdf.text('Second page text', 20, 20)
  for (const file of [
    { name: '测试.docx', mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', buffer: word, expected: 'Word 中文正文测试' },
    { name: '测试.pdf', mimeType: 'application/pdf', buffer: Buffer.from(pdf.output('arraybuffer')), expected: 'Second page text' },
  ]) {
    await page.goto('/')
    await page.getByLabel('上传文档', { exact: true }).setInputFiles(file)
    await expect(page.locator('.report-chapter')).toContainText(file.expected, { timeout: 15000 })
  }
})

test('无效、空白、损坏和超限文件可重试且不创建报告', async ({ page }) => {
  await page.goto('/')
  const input = page.getByLabel('上传文档', { exact: true })
  for (const file of [
    { name: '旧版.doc', buffer: Buffer.from('old'), expected: '另存为 .docx' },
    { name: '空白.md', buffer: Buffer.from('  \n'), expected: '未找到可导入的正文' },
    { name: '空文件.md', buffer: Buffer.alloc(0), expected: '文件为空' },
    { name: '其他.exe', buffer: Buffer.from('test'), expected: '请选择 PDF' },
    { name: '过大.md', buffer: Buffer.alloc(20 * 1024 * 1024 + 1), expected: '文件超过 20 MB' },
  ]) {
    await input.setInputFiles({ ...file, mimeType: 'application/octet-stream' })
    await expect(page.getByRole('alert')).toContainText(file.expected)
    await expect(input).toBeEnabled()
  }
  await input.setInputFiles({ name: '损坏.docx', mimeType: 'application/octet-stream', buffer: Buffer.from('broken') })
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(input).toBeEnabled()
  const pdf = new jsPDF()
  await input.setInputFiles({ name: '无文字.pdf', mimeType: 'application/pdf', buffer: Buffer.from(pdf.output('arraybuffer')) })
  await expect(page.getByRole('alert')).toContainText('OCR', { timeout: 15000 })
  await expect(page.locator('.report-card')).toHaveCount(3)
  await input.setInputFiles({ name: '重试.md', mimeType: 'text/markdown', buffer: Buffer.from('重试成功') })
  await expect(page.getByRole('heading', { name: '重试', exact: true })).toBeVisible()
})
