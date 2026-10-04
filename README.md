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
- 上传 PDF、Word（.docx）、Markdown（.md / .markdown），在浏览器本地提取正文并保存为可阅读文档；单文件最大 20 MB，PDF 最多 300 页，正文最多 50 万字。
- 导出下拉框支持 PDF、Word（.docx）、Markdown（.md）和独立 HTML，包含目录、完整正文及图表对应的数据；不受阅读页搜索筛选影响。
- 浏览器 localStorage 保存报告和书签，桌面及移动布局。

## 范围与限制

生成的调研报告使用固定的前端演示模板，图表数值为虚构数据，不是真实 AI 调研结果。不提供模型选择。当前没有后端、真实 SSE、登录或服务端托管分享。输入不会发送给任何模型。数据仅保存于当前浏览器，清理站点数据会删除本地记录。

上传文档直接进入阅读，不参与模拟调研生成。导入仅提取文字，不保留原始图片、复杂排版或 Word 样式；Markdown 标题转换为章节，其余内容按文本显示。旧版 .doc 需先另存为 .docx；扫描版 PDF 需先进行 OCR。PDF 导出使用浏览器字体渲染为分页图片，支持中文但文字不可选中、搜索，也无法由本项目再次提取正文；需要可编辑文字时请导出 Word 或 Markdown。格式处理依赖按需加载，文件不会上传服务器。

会议纪要中的 Python 前端原型按当前开发要求改为 Vue 3 + TypeScript。优先验证“问题输入 → 生成反馈 → 结构化可视化阅读”路径。

## 接口接入位置

- `src/services/research.ts`：`generateResearch(request, onEvent, signal)` 提供请求、进度通知、取消和最终报告的契约。现在是定时模拟；后续用 `fetch`、`ReadableStream` 和 SSE 解析替换实现。
- `src/types.ts`：`ResearchRequest`、`GenerationEvent`、`Report`、`Chapter` 数据类型。
- `src/stores/research.ts`：任务状态、报告及书签持久化。
- `src/data/reports.ts`：独立演示内容。接入后端时删除模板生成逻辑。
- `src/services/export.ts`：转义文本并导出静态 HTML。
- `src/services/import.ts`：本地 PDF、Word 和 Markdown 正文提取及章节转换。
- `src/services/documentExport.ts`：PDF、Word、Markdown 导出及 HTML 导出分发。

生产接入需要补充后端检索与来源引用、模型调度、OCR 与复杂文档解析及报告持久化接口。模型凭证只应放在服务端。
