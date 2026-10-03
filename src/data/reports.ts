import type { Report } from '../types'

export function createDemoReport(question: string, category = '自定义调研', deep = true): Report {
  return {
    id: crypto.randomUUID(),
    title: question,
    question,
    category,
    summary: `围绕「${question}」，展示从问题拆解、技术比较到落地建议的报告结构。本文为本地模板演示，未联网检索，也不代表真实调研结论。`,
    createdAt: new Date().toISOString(),
    minutes: deep ? 8 : 4,
    bookmarks: [],
    chart: [
      { label: '方案 A · 快速验证', value: 82 },
      { label: '方案 B · 模块编排', value: 66 },
      { label: '方案 C · 定制训练', value: 43 },
    ],
    chapters: [
      {
        id: 'overview', title: '摘要与核心问题',
        paragraphs: [
          `本次调研问题：${question}。开始正式调研前，需要明确使用者、应用场景与决策目标，以此界定报告的范围。`,
          '这份原型将报告拆分为独立章节，并用可视化辅助比较。所有正文均为演示模板，图表数值是虚构示例；真实产品需要以可追溯的信息来源支撑每个关键结论。',
        ],
      },
      {
        id: 'landscape', title: '现状与研究框架',
        paragraphs: [
          '研究框架可以从用户需求、已有方案和实施约束三个维度展开。首先识别问题发生的具体环节，再比较不同方案对该环节的改善程度。',
          '建议将可验证的事实、分析推论和待确认假设分别记录。对于时间敏感的信息，应标注来源日期，并保留原始材料以供复核。',
        ],
      },
      {
        id: 'comparison', title: '方案比较与可视化',
        paragraphs: [
          '下图仅演示报告中的交互图表。评分采用 0–100 的虚构数值，不来源于市场研究或实际测评，不应作为方案选择的依据。',
          '正式比较时，可根据业务目标设置评估维度，例如输出质量、可控性、集成难度和维护投入。权重应由团队确认，再使用同一组任务进行对照验证。',
        ],
      },
      ...(deep ? [{
        id: 'roadmap', title: '落地路径与验证',
        paragraphs: [
          '第一阶段聚焦端到端可用性：从输入问题，到展示生成状态，再到阅读结构化报告。以少量典型任务收集反馈，确认报告的组织方式和信息密度。',
          '第二阶段独立评估文本质量与图表质量。为内容保留证据与引用，检查图表中的指标定义、单位和取值是否与正文一致。',
          '第三阶段根据使用反馈调整生成流程，逐步接入文档解析、真实检索与服务端托管。当前前端只展示本地模拟流程。',
        ],
      }] : []),
      {
        id: 'sources', title: '信息来源与待验证项',
        paragraphs: [
          '来源状态：当前报告没有外部文献引用，全部内容由前端固定模板生成。接入后端后，此处应展示资料标题、链接、发布时间和与正文的关联。',
          '待验证事项：问题范围是否足够明确；核心判断是否有独立证据；不同方案是否使用一致的评价标准；建议是否符合团队实际资源。',
        ],
      },
    ],
  }
}

export function initialReports(): Report[] {
  return [
    { title: 'AI Agent：从概念到实际应用', category: '人工智能', date: '2026-10-02T10:00:00.000Z' },
    { title: 'RAG 技术路线与企业知识库实践', category: '技术洞察', date: '2026-10-01T08:00:00.000Z' },
    { title: 'AI 调研产品的可视化表达探索', category: '产品研究', date: '2026-09-30T09:00:00.000Z' },
  ].map((item, index) => ({ ...createDemoReport(item.title, item.category), id: `demo-${index + 1}`, createdAt: item.date }))
}
