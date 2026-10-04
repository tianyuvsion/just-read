import type { Report } from '../types'

const escapeHtml = (value: string) => value.replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!)

export function exportReport(report: Report) {
  const html = `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${escapeHtml(report.title)}</title><style>body{max-width:800px;margin:60px auto;padding:0 24px;font:16px/1.9 system-ui;color:#1f2329;background:#fff}p{white-space:pre-wrap;overflow-wrap:anywhere}h1{line-height:1.4}h2{margin-top:48px}a{color:#1677ff}.notice{padding:16px;background:#e6f4ff}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:12px;border-bottom:1px solid #ddd}</style></head><body><p>JUST READ / RESEARCH REPORT</p><h1>${escapeHtml(report.title)}</h1><p class="notice">${report.source === 'import' ? '本地导入文档 · 仅提取文字，原始图片与排版未保留。' : '演示报告 · 固定模板生成，未经检索核验。图表数据为虚构示例。'}</p><nav>${report.chapters.map(c => `<p><a href="#${escapeHtml(c.id)}">${escapeHtml(c.title)}</a></p>`).join('')}</nav>${report.chapters.map(c => `<section id="${escapeHtml(c.id)}"><h2>${escapeHtml(c.title)}</h2>${c.paragraphs.map(p => `<p>${escapeHtml(p)}</p>`).join('')}${c.id === 'comparison' ? `<table><caption>示例方案评分（虚构，0–100）</caption><thead><tr><th>方案</th><th>评分</th></tr></thead><tbody>${report.chart.map(item => `<tr><td>${escapeHtml(item.label)}</td><td>${item.value}</td></tr>`).join('')}</tbody></table>` : ''}</section>`).join('')}</body></html>`
  const url = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `${report.title.replace(/[\\/:*?"<>|\x00-\x1F]/g, '_').slice(0, 80)}.html`
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
