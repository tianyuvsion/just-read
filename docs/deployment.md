# 配置与部署

此版本采用一个 FastAPI 进程、进程内异步 worker 和一个 SQLite 数据库。前端使用 HTTP 轮询，模型请求 `stream=false`；不需要 SSE、WebSocket、Redis 或独立任务网关。

## 环境变量

后端配置优先级为：已有进程环境变量 > 项目根 `.env.local` > 项目根 `.env` > 代码默认值。机器上的真实凭证放在 `.env.local`，模板与普通配置放在 `.env`；二者均由 `.gitignore` 和 `.dockerignore` 排除，凭证不得放入前端变量或提交 Git。配置在进程启动时读取，修改文件不会更新已运行进程的设置。下面均为 `JUSTREAD_` 前缀。

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `DATABASE_PATH` | `./data/justread.sqlite3` | SQLite 文件位置；相对路径以启动工作目录为准 |
| `LLM_PROVIDER` | `openai` | `openai` / `anthropic` / `openrouter`；`fixture` 仅测试 |
| `LLM_MODEL` | 空 | 服务端固定模型，必须支持结构化输出；OpenRouter 使用其模型页的完整 `vendor/model` ID |
| `LLM_API_KEY` | 空 | 模型提供方凭证 |
| `LLM_BASE_URL` | 空 | OpenAI 默认 `https://api.openai.com/v1`，追加 `/responses`；Anthropic 默认 `https://api.anthropic.com/v1`，追加 `/messages`；OpenRouter 默认 `https://openrouter.ai/api/v1`，追加 `/chat/completions` |
| `SEARCH_PROVIDER` | `auto` | `auto` / `tavily` / `openrouter`；自动选择规则见下文 |
| `SEARCH_API_KEY` | 空 | 仅 Tavily 使用；OpenRouter 检索复用 `LLM_API_KEY` |
| `SEARCH_BASE_URL` | `https://api.tavily.com` | 仅 Tavily API 根地址；OpenRouter 检索使用 `LLM_BASE_URL` |
| `PROVIDER_TIMEOUT_SECONDS` | `60` | 单次外部请求超时，正数 |
| `TASK_TIMEOUT_SECONDS` | `1800` | 任务从接受开始的总期限，含排队；正数 |
| `MAX_SOURCES` | `8` | 来源数量上限，1–20 |
| `WORKERS` | `2` | 单进程内并发任务数，1–16；不是 Uvicorn 进程数 |
| `MAX_ACTIVE_TASKS` | `100` | 全局 `queued` + `running` 任务上限 |
| `TERMINAL_RETENTION_SECONDS` | `86400` | 任务终态及对应幂等记录的可查询期限，默认 24 小时 |
| `SESSION_TTL_SECONDS` | `2592000` | 匿名会话存续期限，默认 30 天；以服务端续期行为为准 |
| `SESSION_COOKIE_SECURE` | `false` | HTTPS 对外服务应设为 `true`；本机 HTTP 开发用 `false` |
| `PUBLIC_ORIGIN` | 空 | 对外访问的完整 Origin，例如 `https://read.example.com`，不含路径 |
| `STATIC_DIR` | `../dist` | 直接启动的默认静态目录；示例 `.env` 为 `./dist`，开发脚本为项目绝对 `dist` |
| `TEST_MODE` | `false` | 测试模式开关；正常部署保持 `false` |
| `FIXTURE_DELAY_SECONDS` | `0.1` | 模拟提供方的步骤延迟，仅测试使用 |

`SEARCH_PROVIDER=auto` 在 `LLM_PROVIDER=openrouter` 且没有 Tavily Key 时选择 OpenRouter，其他情况选择 Tavily。显式 `SEARCH_PROVIDER=openrouter` 只允许搭配 `LLM_PROVIDER=openrouter`；原生 OpenAI、Anthropic 可搭配 Tavily。

默认宽松生成，没有独立搜索配置或没有可用来源时仍尽量生成报告。引用、数字、审查、计划及布局方面的问题记录为提示，不作为业务质量门槛；模型可补充自身知识并标注。报告 `generation-notes` 章节展示待核实项，内部 `_evidence.validation.mode=relaxed` 记录校验模式。图表不再标称为已核验的原文直接值。模型访问、协议、取消、总期限、会话隔离和密钥保护仍然生效。

`health.ready` 仅检查模型提供方、模型名和模型 Key 是否配置，不要求搜索配置，也不会预先验证权限、配额或响应格式。`health.runtime` 另外提供预期/存活 worker 数、运行任务数、数据库故障次数及运行健康状态；它不替代真实模型探测。生产选择 `fixture` 且 `TEST_MODE=false` 时，配置校验拒绝启动。测试须同时设置 `TEST_MODE=true` 与 `LLM_PROVIDER=fixture`，生成报告标记为 `source=demo`。

## OpenRouter

本机设置 `JUSTREAD_LLM_PROVIDER=openrouter`、`JUSTREAD_LLM_BASE_URL=https://openrouter.ai/api/v1`，仅填写一个 `JUSTREAD_LLM_API_KEY`，无需独立搜索配置。当前选择 [OpenAI: GPT-6 Luna](https://openrouter.ai/openai/gpt-6-luna/)，`JUSTREAD_LLM_MODEL=openai/gpt-6-luna`；以后更换时复制实际完整 ID。原生 OpenAI Responses 和 Anthropic Messages 仍保留，`.env.example` 默认模型提供方仍为 `openai`、检索为 `auto`。

结构化生成使用非流式 Chat Completions、JSON Schema `response_format`（`strict=false`）和 `provider.require_parameters=false`。接口格式校验保留，业务缺项和质量检查转为补全或提示；详见 [Quickstart](https://openrouter.ai/docs/quickstart) 和 [Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs)。

检索另发非流式 Chat Completions 请求，`tools` 使用 `type=openrouter:web_search`，参数为 `engine=exa`、`max_uses=1`、`max_results=max_total_results=MAX_SOURCES`、`max_characters=4000`，顶层 `max_tool_calls=1`。不使用已弃用的 web plugin 或 `:online`。`url_citation.content` 作为原文摘录保存，模型补充内容另行标注；没有摘录则记录缺口并继续。该工具为 Beta，搜索经 OpenRouter 额外计费，不需要 Exa Key，详见 [Web Search](https://openrouter.ai/docs/guides/features/server-tools/web-search)。

在项目根运行 `backend/.venv/bin/python scripts/test-live-api.py --check-config` 作无网络配置检查；`--model-only` 检查模型请求，`--search-only` 单独检查来源 URL 和摘录并只打印计数，`--full` 检查完整 brief 调研。独立检索探测的结果不再是报告生成的质量门槛。请求数随计划及重试变化，联网 HTTP 请求含服务端内部推理，不等同于总推理次数或固定费用。完整命令见 [README](../README.md#openrouter-配置)；根目录虚拟环境改用 `.venv/bin/python`。配置说明不代表完整真实调研已通过。

## 同源开发

按 [README](../README.md) 安装依赖后，运行 `sh scripts/dev-backend.sh` 和 `pnpm dev`。后端监听 `127.0.0.1:8000`，Vite 将 `/api` 代理到后端，浏览器始终向前端同源地址发送 Cookie。

开发脚本依次选择 `JUSTREAD_PYTHON`、项目根 `.venv/bin/python`、`backend/.venv/bin/python`。`--reload` 仅用于开发，重载会中断运行中的任务。直接预览构建产物可执行 `pnpm build` 后启动后端，访问 `http://127.0.0.1:8000`；`pnpm preview` 仍需单独启动后端。

`/docs` 和 `/openapi.json` 默认开放，便于本机联调。对外部署可在反向代理限制这些路径的访问；当前没有应用内关闭文档的配置开关。

## Docker Compose

```sh
cp -n .env.example .env
# 创建 .env.local，在其中填写本机模型名称与凭证；不要覆盖已有文件
touch .env.local
chmod 600 .env.local
docker compose --env-file .env --env-file .env.local up --build -d
docker compose ps
```

Compose 默认只自动读取 `.env`，不会自动读取 `.env.local`；上例显式指定两个文件，后一个覆盖前一个，同名进程变量仍优先。配置文件均不复制进镜像。构建阶段固定 pnpm 12.8.1 并使用 `--frozen-lockfile`；运行阶段为 Python 3.12，以非 root 用户运行单个 Uvicorn 进程。镜像安装 Poppler、Tesseract 及中英文语言包，供扫描 PDF 的本地 OCR 使用。

默认只把端口发布到宿主 `127.0.0.1:8000`。前端静态文件位于 `/app/dist`，数据库位于 `/app/data/justread.sqlite3`；`justread-data` 命名卷保存数据库。Compose 将 `TEST_MODE` 固定为 `false`，不会继承 `.env` 中测试开关；数据库和静态目录也使用容器固定路径。

Docker HEALTHCHECK 检查 `/api/v1/health` 的 HTTP 200 和 `status=ok`，用于存活判断；探针在容器 `/tmp` 复用自己的匿名 Cookie，避免每次探测都新建会话。缺少模型凭证时容器仍可健康，调研创建会返回 503；部署系统判断生成能力时，应分别检查 JSON 的 `ready`、`runtime.healthy` 与一次受控的真实模型探测，不要仅凭容器存活状态判断。

CI 新增独立容器任务：构建生产镜像，使用显式 fixture 检查首页、API、OCR 可执行文件、生成与容器重启后的数据保存，不调用真实供应商。该任务需要首次上传后在 CI 实际执行；配置存在不代表执行通过。本机没有 Docker，因此本机容器构建与运行仍未验收。真实供应商联调也不能由 fixture 容器检查替代。

## HTTPS 反向代理

将同一个域名的所有路径转发到宿主 `127.0.0.1:8000`，保留 Host，并按代理部署设置转发头。配置：

```dotenv
JUSTREAD_PUBLIC_ORIGIN=https://read.example.com
JUSTREAD_SESSION_COOKIE_SECURE=true
```

客户端应使用这一同源 URL，不应直接暴露两个不同域名的前后端。当前不配置跨域凭证共享。Cookie 是工作空间访问凭证，不能写入页面脚本或公开日志。

## 材料、版本与离线交付

- 研究材料通过 sources 接口保存原文件 BLOB、SHA-256、提取页/段落和 chunk。支持 PDF、DOCX、Markdown、TXT、CSV、JSON；单文件最大 20 MB，PDF 最多 300 页。材料选择进入 ResearchSpec，供 A/B 阶段建立可定位引用。材料与直接导入阅读报告是两条独立入口。
- PDF 先提取文本，对空白页尝试 OCR。macOS 优先使用本机 Swift + Apple Vision；Linux 容器使用 Poppler + Tesseract 中英文模型。OCR 失败或工具不可用时保留原文件和缺口提示；识别结果不代表数字、符号或引用已核实。DOCX 使用段落定位，不承诺保留原始版式或页码。
- WorkspaceStore 采用增量建表迁移，保留原 tasks/reports；旧报告转为不可变的版本 1。首次部署新版本前仍需备份整个数据库。版本正文、来源快照与成功任务状态在同一事务写入，任何一步失败均回滚。
- 编辑带 `base_version`；过时版本返回 409，避免覆盖他人的修改。恢复旧版本会新建版本。章节书签是个人阅读状态，不改变正文版本。AI 审查、自动一致性检查、人工审查分别记录；人工修改后原 AI 审查标记为旧版本结果。
- 分享操作冻结指定报告版本的公开投影，拥有独立过期时间并可撤销；无需登录的公开接口只能读取该分享快照。它保留正文、图示、已引用摘录与定位，去除旧版本可能携带的 raw_content/pages/chunks 全文和密钥类字段；公开 manifest 单独记录公开正文哈希和原版本正文哈希。它不会随随后编辑而变化，也不会开放整个工作空间、原材料下载或内部研究快照。
- HTML/ZIP 导出固定版本，含离线阅读/检索、全部流程与关系图、柱形/折线/数据表，以及有依据时的 box/sphere/cylinder 参数化三维模型。无 CDN 或远程模型资产。三维可以旋转、缩放、爆炸、选择，并附无外链 `scene.glb`；示意位置和尺寸不冒充实测值。
- ZIP 内 `manifest.json` 为每个文件记录 SHA-256，`verify.py` 可独立校验；manifest 不计算自己的哈希。正文哈希排除个人书签和 manifest 字段，避免自引用。`reviews.json` 冻结导出时的该版本审查记录；新增审查使导出缓存失效，历史导出快照仍保留。owner 的 HTML/ZIP 导出与公开分享不同：导出包含 `evidence.json` 内部来源和原文记录，应按研究资料包管理。

## 数据与故障行为

- 同一匿名会话最多一个正在运行/排队任务。并发 worker 共享 SQLite。每次领取任务生成独立 `execution_id`；暂停、取消、恢复会使旧标识失效。检查点、进度、失败与成功发布均检查当前执行标识，旧执行的迟到写入不能覆盖恢复后的执行。
- 暂停保留已完成检查点；继续使用同一 task ID，重试创建新任务并按所选 A/B/C/D 阶段复制上游检查点。管线核对输入摘要后才复用。中断时尚未成功保存的外部调用可能需要重新请求，不能承诺暂停/取消免除其费用。
- 进程重启后，之前 `running` 的任务标记为 `failed/SERVER_RESTARTED`；`queued` 的任务继续按原期限处理，已暂停任务保持暂停。用户可显式重试已有检查点，不会自动重新发起可能已经收费的完整调研。
- worker 遇到暂时性数据库故障会保留循环并重试，`health.runtime` 反映故障与存活情况。调用用量/耗时及阶段记录关联 task/execution；没有供应商返回费用时，不将 token 数换算成虚构金额。
- 任务终态默认保留 24 小时，过期后任务查询返回 404，同一幂等键不再保证复用；成功报告独立保留，可从报告接口读取。
- SQLite 的 `report_artifacts` 保存计划、可获得的来源摘录及 URL/抓取时间、结论、缺口、图表数据、审查、编排及宽松校验记录，与报告及成功状态同事务提交。完整对象不通过 HTTP 返回，待核实提示出现在公开的 `generation-notes` 章节；fixture 只保存模拟标记，导入没有研究证据快照。备份整个数据库会同时包含报告与内部记录，不能仅导出 reports 表作为完整研究记录。
- health 请求会续期会话。会话过期后即使数据库仍有报告，浏览器也无法访问原会话数据。当前没有账户恢复、报告删除或过期会话数据自动清理接口，应自行设定保留策略后再扩大部署范围。对外内测仍应通过反向代理配置访问控制；分享链接是单版本读取能力，不是账户系统。
- 报告列表一次返回当前会话全部报告，没有分页；可选 q 仅作内存文本匹配，没有全文索引。适用于当前小规模单实例，数据库磁盘、会话数量和报告数量需要运营侧控制。

升级前备份 SQLite。简单方式是停止服务后备份整个数据卷，避免复制正在写入的单个 `.sqlite3` 文件而遗漏 WAL 内容；完成后启动服务。恢复时保持数据库目录可由容器 UID 10001 写入。`docker compose down` 保留命名卷，`docker compose down -v` 会删除数据，日常重启不要使用 `-v`。

本版具有上述增量 SQLite 表迁移，但没有通用迁移/回滚框架、分布式租约、多副本故障接管或管理鉴权。不要通过 `uvicorn --workers N` 或多个容器共享数据库扩容；先引入独立队列与支持并发部署的存储，再修改运行方式。备份需覆盖材料原文件、检查点、所有报告版本、审查/分享记录和离线导出历史所在的整个数据库。
