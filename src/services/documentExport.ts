import type { Report } from '../types'
import { exportReport as exportHtml } from './export'
import { reportNotice } from './research'

export type ExportFormat = 'pdf' | 'docx' | 'md' | 'html'
const escapeMarkdown = (value: string) => value.replace(/([\\`*_{}\[\]()<>#+.!|~-])/g, '\\$1')

function blocks(report: Report): { text: string; heading?: 1 | 2 }[] {
  return [
    { text: report.title, heading: 1 },
    { text: reportNotice(report) },
    { text: report.summary }, { text: '目录', heading: 2 }, ...report.chapters.map(c => ({ text: c.title })),
    ...report.chapters.flatMap(c => [
      { text: c.title, heading: 2 as const }, ...c.paragraphs.map(text => ({ text })),
      ...(c.id === (report.chapters.find(item => item.id === 'comparison')?.id ?? report.chapters[0]?.id) && report.chart.length && report.chartMeta ? [
        { text: `${report.chartMeta!.title}（${report.chartMeta!.unit}）` },
        { text: report.chartMeta!.note },
        ...report.chart.map(item => ({ text: `${item.label}：${item.value} ${report.chartMeta!.unit}` })),
      ] : []),
    ]),
  ]
}

// Browser fonts preserve CJK without fetching a font. PDF pages are raster images.
async function pdfBlob(report: Report) {
  const { jsPDF } = await import('jspdf')
  await document.fonts.ready
  const pdf = new jsPDF({ unit: 'pt', format: 'a4' })
  const canvas = document.createElement('canvas')
  canvas.width = 1190
  canvas.height = 1684
  const context = canvas.getContext('2d')
  if (!context) throw new Error('浏览器不支持 PDF 渲染。')
  const margin = 90
  let y = margin
  let pages = 0
  function reset() {
    context!.fillStyle = '#fff'
    context!.fillRect(0, 0, canvas.width, canvas.height)
    y = margin
  }
  function flush() {
    if (pages++) pdf.addPage()
    pdf.addImage(canvas.toDataURL('image/png'), 'PNG', 0, 0, 595.28, 841.89, undefined, 'FAST')
    reset()
  }
  reset()
  for (const block of blocks(report)) {
    const size = block.heading === 1 ? 44 : block.heading === 2 ? 32 : 25
    const lineHeight = size * 1.7
    context.font = `${block.heading ? '600' : '400'} ${size}px "Microsoft YaHei", "PingFang SC", sans-serif`
    const drawLine = (line: string) => {
      if (y + lineHeight > canvas.height - margin) flush()
      context.fillStyle = '#1f2329'
      context.fillText(line, margin, y + size)
      y += lineHeight
    }
    for (const paragraph of block.text.split('\n')) {
      let line = ''
      for (const character of paragraph) {
        if (line && context.measureText(line + character).width > canvas.width - margin * 2) {
          drawLine(line)
          line = ''
        }
        line += character
      }
      drawLine(line)
    }
    y += 22
    await new Promise<void>(resolve => setTimeout(resolve, 0))
  }
  if (y > margin) flush()
  return pdf.output('blob')
}

export async function exportDocument(report: Report, format: ExportFormat) {
  if (format === 'html') return exportHtml(report)
  let blob: Blob
  const content = blocks(report)
  if (format === 'pdf') {
    blob = await pdfBlob(report)
  } else if (format === 'docx') {
    const { Document, Packer, Paragraph, TextRun, HeadingLevel } = await import('docx')
    const doc = new Document({ sections: [{ children: content.map(block => new Paragraph({
      heading: block.heading === 1 ? HeadingLevel.TITLE : block.heading === 2 ? HeadingLevel.HEADING_1 : undefined,
      children: block.text.split('\n').map((text, index) => new TextRun({ text, break: index ? 1 : undefined })),
      spacing: { after: 180 },
    })) }] })
    blob = await Packer.toBlob(doc)
  } else {
    const markdown = content.map(block => `${block.heading ? '#'.repeat(block.heading) + ' ' : ''}${escapeMarkdown(block.text)}`).join('\n\n') + '\n'
    blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' })
  }
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${report.title.replace(/[\\/:*?"<>|\x00-\x1F]/g, '_').slice(0, 80) || '报告'}.${format}`
  document.body.append(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}
