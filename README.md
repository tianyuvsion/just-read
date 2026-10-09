# JustREAD

Vue 3 / TypeScript 前端与 FastAPI / SQLite 后端，已实现 A–D 研究工作流：材料与证据 → 研究与计算 → 阅读图解与概念三维 → 审查、版本和交付。调研采用任务轮询，模型调用均为非流式，OpenRouter 模型和检索可共用一个 Key。

## 本地启动

需要 Python 3.11+、Node.js 22.13+ 和 pnpm 12.8.1。在项目根目录执行：

```sh
pnpm install --frozen-lockfile
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt
cp -n .env.example .env
```

编辑 `.env.local`（本机专用）或 `.env`，设置 `JUSTREAD_LLM_MODEL` 和 `JUSTREAD_LLM_API_KEY`。读取顺序为进程环境变量 → `.env.local` → `.env`。OpenRouter 共用一个 Key，无需 Tavily；两种文件均排除 Git，凭证只由后端读取。供应商支持 `openai`、`anthropic`、`openrouter`，模型需支持结构化输出。

两个终端分别启动：

```sh
sh scripts/dev-backend.sh
```

```sh
pnpm dev
```

打开前端终端显示的地址，默认 `http://127.0.0.1:5173`；Vite 将 `/api` 代理给 `http://127.0.0.1:8000`。后端交互文档位于 `http://127.0.0.1:8000/docs`，OpenAPI 位于 `/openapi.json`。

未配置模型凭证时，服务可启动，`GET /api/v1/health` 返回 `ready: false`；创建调研返回 503，不会退回演示结果。`ready: true` 仅表示模型配置具备，不要求搜索配置，也不代表已经验证模型授权、额度或外网连通性。

## A–D 功能

| 阶段 | 当前实现 |
| --- | --- |
| A 目标与材料 | 读者/范围/深度、材料库、多选材料参与调研；PDF/DOCX/MD/TXT/CSV/JSON 原件保存、哈希、页码/段落与片段定位；网络检索；扫描 PDF 本地 OCR |
| B 研究与计算 | 子问题分析、证据关联、ResearchIR、覆盖率/矛盾/未知；自动核算回流与主动复算；输入、公式、单位及来源可追溯 |
| C 阅读与表达 | 阅读计划、语义块、流程/关系/条形/折线/表格；概念三维 mesh 的旋转、缩放、爆炸、选择和部件编辑；SceneSpec JSON 包导入导出 |
| D 审查与交付 | 程序一致性、整篇 AI 阅读审查与单轮有限表达修订、独立人工审查；版本编辑/比较/恢复、乐观并发冲突保护；固定分享/到期/撤销、自包含 HTML/ZIP 及 GLB/哈希清单 |

任务支持取消、暂停、刷新续跑、按 A/B/C/D 重新执行、阶段 checkpoint 和用量记录。每次执行有独立标识，旧执行的迟到结果不能覆盖新执行。报告、证据和版本在同一事务发布；旧库自动升级为版本1，不删除旧报告。

研究材料通过“添加研究材料”上传到后端并参与研究；原有“上传文档”在浏览器提取正文后导入阅读，不运行研究。原件最大20 MB，PDF最多300页；材料文字最大100万字符，阅读报告正文最大50万字符。macOS OCR使用Swift/PDFKit/Vision；Linux部署使用Poppler+Tesseract中英语言包，Docker已配置。OCR数字和符号需原页复核，复杂表格语义与嵌入图像理解暂未实现。

原有搜索、章节导航、书签和PDF/DOCX/Markdown导出继续保留。PDF为图片分页，文字不可选择；新固定版本HTML/ZIP包含可交互图解与三维，不依赖远程CDN。解压后运行 `python3 verify.py` 检查完整性。概念三维不代表实测尺寸；不支持任意CAD/GLB导入、物理仿真或真实对象自动重建。

默认采用宽松生成：没有来源、引文不匹配、审查未通过、计划或布局缺少字段或数量不同，都会转为提示并尽量生成可阅读报告。模型可补充自身知识，但须标明；公开的 `generation-notes` 章节集中展示待核实项。图表统一标为候选数据、待核实，允许负数或单个数据点，不承诺已核验的原文直接值。

来源以报告“来源”章节中的文本引用展示。`source=demo` 为显式演示内容，`generated` 为生成报告，`import` 为导入文档；任务成功只表示报告已生成并保存，不代表事实或研究质量通过审核。

调研保存计划、来源原文与定位、结论、缺口、数据、计算、审查和编排，内部快照与版本原子提交。公开Report只含来源元数据和实际引用摘录，完整未引用材料留在私有库。fixture的报告和审查明确标模拟，不包含真实研究结果。

匿名工作空间尚无账户登录、跨设备恢复、报告删除或管理后台。分享链接允许其他浏览器只读对应固定版本；编辑与材料管理仍需原会话。清除 Cookie 或到期后无法恢复工作空间。SQLite 与队列按单实例设计，不直接增加 Uvicorn进程或复制容器。

## 验证

```sh
.venv/bin/python -m pytest backend/tests -q
pnpm build
pnpm exec playwright install chromium
pnpm test:e2e
```

Playwright 覆盖桌面和手机，自动在8001/5187启动隔离后端/前端，禁用dotenv，使用明确标注的fixture，不读取本机Key。端口可由 `JUSTREAD_E2E_API_PORT` / `JUSTREAD_E2E_FRONTEND_PORT` 覆盖；Python可由 `JUSTREAD_PYTHON` 指定。CI执行后端、构建与Chromium E2E。

开发启动脚本优先使用 `JUSTREAD_PYTHON`，否则查找根目录 `.venv`，最后回退到 `backend/.venv`；测试命令中的解释器路径也应使用实际创建的虚拟环境。

这些测试验证接口和用户流程，**模拟结果不等于真实检索/模型 API 联调**。真实联调需配置可用凭证，手动完成一次创建、轮询成功、核对来源、刷新恢复及取消；失败原因可从任务 `error` 获取。不要在日志、测试截图或提交中附带凭证。

### 本机真实接口检查

在项目根 `.env.local` 填写本机配置，保持 `JUSTREAD_TEST_MODE=false`。原生 `openai` 使用 Responses，`anthropic` 使用 Messages，两者可搭配 Tavily；自定义地址需兼容所选提供方的接口。

#### OpenRouter 配置

当前本地选择 [OpenAI: GPT-6 Luna](https://openrouter.ai/openai/gpt-6-luna/)，完整ID为 `openai/gpt-6-luna`。在 `.env.local` 设置以下字段，填写自己的OpenRouter Key。以后更换时复制完整ID并确认结构化输出支持。

```dotenv
JUSTREAD_LLM_PROVIDER=openrouter
JUSTREAD_LLM_BASE_URL=https://openrouter.ai/api/v1
JUSTREAD_LLM_MODEL=openai/gpt-6-luna
JUSTREAD_LLM_API_KEY=
JUSTREAD_TEST_MODE=false
```

结构化生成调用 `/chat/completions`，设置 `stream=false`、JSON Schema `response_format`（`strict=false`）和 `provider.require_parameters=false`。JSON 格式属于接口约定，业务内容则按宽松模式补全和标注。配置依据 [OpenRouter Quickstart](https://openrouter.ai/docs/quickstart) 和 [Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs)。

模型与 OpenRouter 搜索共用 `JUSTREAD_LLM_API_KEY`，不需要 Tavily 或 Exa Key。检索默认 `auto`：模型提供方为 OpenRouter 且没有 Tavily Key 时选择 OpenRouter，其余选择 Tavily；也可显式设置 `JUSTREAD_SEARCH_PROVIDER=openrouter`，但须搭配 OpenRouter 模型提供方。

检索使用 Beta [Web Search server tool](https://openrouter.ai/docs/guides/features/server-tools/web-search)，固定 Exa 引擎；`url_citation.content` 保存为来源摘录，模型补充内容另行标明。没有可用摘录时可继续生成，并记录材料缺口。搜索费用通过 OpenRouter 额外计费，另有模型 token 费用。

```sh
# 只检查模型配置；不要求搜索 Key，不访问网络、不打印密钥
backend/.venv/bin/python scripts/test-live-api.py --check-config
# 小型真实模型请求；不需要搜索 Key
backend/.venv/bin/python scripts/test-live-api.py --model-only
# 单独探测检索；打印计数，不打印正文或密钥
backend/.venv/bin/python scripts/test-live-api.py --search-only
# 一条真实 brief 调研，验证 HTTP 任务、报告保存及证据快照
backend/.venv/bin/python scripts/test-live-api.py --full
# 完整 A–D 真实验收：模拟电机材料、自动/主动核算、三维、审查、版本、分享与离线包
backend/.venv/bin/python scripts/test-live-ad.py --run
```

若虚拟环境建在根目录，将解释器路径换成 `.venv/bin/python`。`--check-config` 不访问网络，配置就绪不代表真实连通。`--full` 使用独立的 `.test-data/live-*.sqlite3`，不修改日常工作空间；业务 HTTP 通过本进程 TestClient 验证，供应商请求走真实网络。请求数量随实际计划、补全和重试变化；联网 HTTP 请求含服务端内部推理，不能换算为固定总推理次数或费用。`model_ok` 仅表示单模型连接通过；完整流程是否完成看本次 `full_ok`，研究内容中的待核实项仍需阅读 `generation-notes`。模型未配置或无法访问、协议异常、取消和超时仍会阻止任务完成。

`test-live-ad.py --run` 会产生真实供应商费用，结果、数据库、运行记录和HTML/ZIP写入独立的`.test-data/live-ad-<UUID>/`。其中数值是明确标注的模拟软件验收输入；仅用于检查取证、核算和交付功能，不能当作电机实测性能。结果逐项记录实际计算、三维部件、模型审查和表达修订覆盖，不以任务成功代替各模块验收。

## 部署与文档

```sh
cp -n .env.example .env
# 编辑 .env 中的模型和检索配置
docker compose up --build -d
```

访问 `http://127.0.0.1:8000`。Docker 构建前端并由 FastAPI 同源提供静态文件；SQLite 保存在 `justread-data` 命名卷。发布到公网时配置 HTTPS 反向代理、`JUSTREAD_PUBLIC_ORIGIN` 和 `JUSTREAD_SESSION_COOKIE_SECURE=true`。

- [HTTP 接口、错误与任务生命周期](docs/api.md)
- [A–D 操作、验收与实现边界](docs/workflow.md)
- [环境变量、部署和运维限制](docs/deployment.md)
- [本轮测试范围与结果](docs/testing.md)
- [机器可读 OpenAPI](docs/openapi.json)
- [配置示例](.env.example)

后端关键入口为 `backend/just_read/app.py`，前端接口适配为 `src/services/research.ts`，数据类型为 `src/types.ts`。
