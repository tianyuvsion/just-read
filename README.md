# Just Read

根据 2026 年 10 月 2 日产品研讨会纪要搭建的 AI 调研可视化前端原型。采用 Vue 3、TypeScript、Vite、Vue Router、Pinia。

## 启动

```sh
pnpm install
pnpm dev
```

访问终端打印的本地地址（默认 `http://127.0.0.1:5173`）。Node.js 要求 22.13+，包管理器固定为 pnpm 12.8.1。

```sh
pnpm build                       # TypeScript 检查及生产构建
pnpm preview                     # 本地预览生产构建
pnpm exec playwright install chromium
pnpm test:e2e                    # 桌面和移动端核心流程
```

提交 `pnpm-lock.yaml`，CI 使用 `pnpm install --frozen-lockfile`。项目统一使用 pnpm 管理依赖。

## 已实现

- 输入调研问题、推荐问题、默认深度调研、模拟分阶段生成和取消。
- 报告列表、分类筛选、全文搜索、章节阅读和目录导航。
- 演示图表及数据视图、章节书签、报告内关键词高亮。
- 独立 HTML 导出（包含目录、正文及图表对应的数据表）。
- 浏览器 localStorage 保存报告和书签，桌面及移动布局。

## 范围与限制

所有报告使用固定的前端演示模板，图表数值为虚构数据，不是真实 AI 调研结果。不提供模型选择。当前没有后端、真实 SSE、文档上传解析、登录或服务端托管分享。输入不会发送给任何模型。数据仅保存于当前浏览器，清理站点数据会删除本地记录。

会议纪要中的 Python 前端原型按当前开发要求改为 Vue 3 + TypeScript。优先验证“问题输入 → 生成反馈 → 结构化可视化阅读”路径。

## 接口接入位置

- `src/services/research.ts`：`generateResearch(request, onEvent, signal)` 提供请求、进度通知、取消和最终报告的契约。现在是定时模拟；后续用 `fetch`、`ReadableStream` 和 SSE 解析替换实现。
- `src/types.ts`：`ResearchRequest`、`GenerationEvent`、`Report`、`Chapter` 数据类型。
- `src/stores/research.ts`：任务状态、报告及书签持久化。
- `src/data/reports.ts`：独立演示内容。接入后端时删除模板生成逻辑。
- `src/services/export.ts`：转义文本并导出静态 HTML。

生产接入需要补充后端检索与来源引用、模型调度、文件解析及报告持久化接口。模型凭证只应放在服务端。
