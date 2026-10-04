import type { Chapter, Report } from '../types'

export const documentAccept = '.pdf,.docx,.md,.markdown'
const maxBytes = 20 * 1024 * 1024

export async function importDocument(file: File): Promise<Report> {
  const extension = file.name.split('.').pop()?.toLowerCase()
  if (extension === 'doc') throw new Error('暂不支持旧版 .doc，请用 Word 另存为 .docx 后上传。')
  if (!['pdf', 'docx', 'md', 'markdown'].includes(extension ?? '')) throw new Error('请选择 PDF、Word（.docx）或 Markdown（.md）文件。')
  if (!file.size) throw new Error('文件为空，请选择包含正文的文档。')
  if (file.size > maxBytes) throw new Error('文件超过 20 MB，请缩小后重试。')

  let text = ''
  if (extension === 'pdf') {
    const pdfjs = await import('pdfjs-dist')
    const worker = await import('pdfjs-dist/build/pdf.worker.min.mjs?url')
    pdfjs.GlobalWorkerOptions.workerSrc = worker.default
    const task = pdfjs.getDocument({ data: await file.arrayBuffer() })
    try {
      const pdf = await task.promise
      if (pdf.numPages > 300) throw new Error('PDF 超过 300 页，请拆分后上传。')
      for (let pageNumber = 1; pageNumber <= pdf.numPages; pageNumber++) {
        const page = await pdf.getPage(pageNumber)
        const content = await page.getTextContent()
        text += content.items.map(item => 'str' in item ? item.str + (item.hasEOL ? '\n' : ' ') : '').join('') + '\n\n'
        page.cleanup()
        if (text.length > 500_000) throw new Error('正文超过 50 万字，请拆分后上传。')
      }
    } finally { await task.destroy() }
  } else if (extension === 'docx') {
    const mammoth = await import('mammoth')
    text = (await mammoth.extractRawText({ arrayBuffer: await file.arrayBuffer() })).value
  } else {
    text = await file.text()
  }
  text = text.replace(/\r\n?/g, '\n').replace(/\u0000/g, '').trim()
  if (!text) throw new Error(extension === 'pdf' ? '未找到可提取的文字。扫描版 PDF 请先进行 OCR 识别。' : '未找到可导入的正文。')
  if (text.length > 500_000) throw new Error('正文超过 50 万字，请拆分后上传。')
  const chapters: Chapter[] = []
  let current: Chapter = { id: 'import-1', title: '正文', paragraphs: [] }
  let hasHeading = false
  for (const block of text.split(/\n\s*\n/)) {
    for (const part of block.split(/\n(?=#{1,6}\s)/)) {
      const heading = (extension === 'md' || extension === 'markdown') && part.match(/^#{1,6}\s+([^\n]+)(?:\n([\s\S]*))?$/)
      if (heading) {
        if (current.paragraphs.length || hasHeading) chapters.push(current)
        current = { id: `import-${chapters.length + 1}`, title: heading[1]!.trim(), paragraphs: [] }
        hasHeading = true
        if (heading[2]?.trim()) current.paragraphs.push(heading[2].trim())
      } else if (part.trim()) current.paragraphs.push(part.trim())
    }
  }
  if (current.paragraphs.length || hasHeading || !chapters.length) chapters.push(current)
  return {
    id: crypto.randomUUID(), source: 'import', title: file.name.replace(/\.[^.]+$/, '') || '导入文档',
    question: '', category: '导入文档', summary: `从 ${file.name} 导入 · ${text.length.toLocaleString()} 字`,
    createdAt: new Date().toISOString(), minutes: Math.max(1, Math.ceil(text.length / 500)),
    chapters, chart: [], bookmarks: [],
  }
}
