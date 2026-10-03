import { createDemoReport } from '../data/reports'
import type { GenerationEvent, Report, ResearchRequest } from '../types'

export const generationSteps = ['理解调研问题', '组织研究框架', '编排章节与图表', '完成可视化报告']

// 后端接入点：用 fetch + ReadableStream 消费 SSE，并保留此回调与 AbortSignal 契约。
// 当前不会调用模型、执行搜索或发送用户输入。
export async function generateResearch(
  request: ResearchRequest,
  onEvent: (event: GenerationEvent) => void,
  signal: AbortSignal,
): Promise<Report> {
  for (const [step, message] of generationSteps.entries()) {
    signal.throwIfAborted()
    onEvent({ step, message })
    await new Promise<void>((resolve, reject) => {
      const abort = () => { clearTimeout(timer); reject(new DOMException('已取消', 'AbortError')) }
      const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, 850)
      signal.addEventListener('abort', abort, { once: true })
    })
  }
  signal.throwIfAborted()
  return createDemoReport(request.question.trim(), '自定义调研', request.depth === 'deep')
}
