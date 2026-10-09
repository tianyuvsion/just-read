# 验证记录（各版本按日期保留）

验证日期：2026-10-05。运行环境：macOS arm64、Python 3.12.14、Node.js 24.19.0、pnpm 12.8.1、系统 Chrome。

| 检查 | 结果 | 范围 |
| --- | --- | --- |
| `backend/.venv/bin/python -m pytest backend/tests -q` | 46 passed | API、SQLite、任务引擎、提供方协议和研究管线 |
| `vue-tsc -b` 与 `vite build` | 通过 | TypeScript 与生产静态构建 |
| Playwright，桌面与手机视口 | 30 passed | 后端连接、生成/取消/刷新/幂等、导入导出、阅读及书签 |
| OpenAPI 导出与当前代码对比 | 一致 | 8 个路径，含真实 ErrorEnvelope 与报告列表 Schema |
| Docker/Compose | 静态检查通过 | 当前环境未安装 Docker，未构建或运行镜像 |

后端测试覆盖：同键重放和冲突、会话隔离、活跃任务限制、取消与发布并发竞争、迟到结果丢弃、排队与执行超时、重启后的 queued/running 恢复、终态过期、报告独立保存、书签校验、导入服务器身份、请求体与 Origin 限制、拒答与截断、有限重试、引用与数字核验、证据审查失败、图表口径、快照发布回滚、错误脱敏和静态前端同源服务。

提供方测试使用 `httpx.MockTransport` 模拟 OpenAI Responses、Anthropic Messages 和 Tavily 响应。一个集成测试将模拟的真实提供方协议贯穿研究管线、任务引擎、SQLite 和报告 HTTP 读取，验证快照保存且不出现在公开响应中。

浏览器测试使用真实本地 FastAPI 和显式 fixture 提供方。新增回归覆盖跨标签页共享任务记录、没有 Web Locks 时的同步恢复，以及旧取消响应延迟返回时不删除新任务记录；还验证 POST 响应丢失后的原键恢复、已知任务只 GET、未知/超期记录不重建。

以上测试没有调用真实付费模型或真实检索服务，不表示真实账户权限、配额、网络连通性或调研质量已验收。Docker 运行和真实供应商联调尚未执行。pytest 输出包含一条当前 Starlette TestClient 对 httpx 的弃用提示，不影响上述测试结果。

可用模型及参数以供应商官方接口为准：[OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)、[Claude Structured Outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)、[Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)。

## 2026-10-07：OpenRouter 接入与本机联调工具（前一版）

运行 `backend/.venv/bin/python -m pytest backend/tests -q`，结果为 **103 passed**。其中 46 项 OpenRouter 测试使用 `httpx.MockTransport`，覆盖非流式 Chat Completions、JSON Schema、`provider.require_parameters=true`、默认及自定义地址、拒答和截断、异常完成状态、HTTP 200 内嵌错误、有限重试和结构修复，以及完整 brief 管线的来源和内部证据快照。11 项 CLI 测试验证无配置时不联网、单模型探测无需搜索凭证、完整调研要求 Tavily、配置与失败输出不回显密钥、无效命令行参数脱敏和拒绝测试模式。

新增真实联调入口为 `scripts/test-live-api.py`：默认或 `--check-config` 只检查模型配置，不联网；`--model-only` 发起小型结构化模型请求；`--full` 通过业务 HTTP 测试一条真实 brief 调研，并使用独立数据库验证报告和证据保存。未填 Tavily 时，配置检查可通过且输出 `ready=false`；这不代表完整调研可用。

本次本地选择 `openai/gpt-6-luna`，模型 ID 及结构化输出支持已由 [OpenRouter 模型页](https://openrouter.ai/openai/gpt-6-luna/) 核对。实际模型和搜索 Key 尚未填写，**没有执行真实 API 联调**。本次未修改前端，未重复运行前端构建或浏览器测试；2026-10-05 的记录仍为历史结果。pytest 保留一条 Starlette TestClient 弃用提示。

## 2026-10-07：OpenRouter 单 Key 检索与真实本地实验（历史严格模式）

本版模型和检索均可使用 OpenRouter Key，不要求 Tavily。联网请求使用 `openrouter:web_search` 服务端工具，指定 Exa 引擎；只采用 `url_citation.content` 返回的原文摘录，保留来源、查询及摘录类型，不采用生成回答作为证据。每请求最多一次搜索，截断、拒答、异常完成状态及 HTTP 200 内嵌错误均被拒绝。该服务端工具目前为 Beta，协议依据 [OpenRouter 文档](https://openrouter.ai/docs/guides/features/server-tools/web-search)。

| 检查 | 本次结果 | 证据边界 |
| --- | --- | --- |
| 全套后端回归 | **171 passed** | MockTransport/fixture；含单 Key 完整 HTTP 流程、报告原子保存、证据失败不发布、引文修复及预算检查 |
| 桌面/手机浏览器冒烟 | **2 passed** | 本地 fixture；生成、书签、阅读搜索与导出，非真实模型质量验收 |
| 真实 `--model-only` | **model_ok** | 本机 OpenRouter Key 与结构化输出连接通过 |
| 真实 `--search-only` | **search_ok** | 8 个合格来源，摘录合计 5662 字符；这是单项检索结果 |
| 首次真实 `--full` | **failed / INVALID_EVIDENCE** | 引文不在对应原文或来源编号无效；没有发布报告 |
| 引文修正后的真实重跑 | **待执行** | 本机 `.env` 在实验期间变为空模型/Key；非密钥配置已恢复，Key 尚需本机重新填写 |

首次完整实验问题为“Python asyncio 中 TaskGroup 与 gather 的异常传播和取消行为有什么区别？”，深度为 `brief`，任务 ID 为 `ce85d384-a979-4319-accb-c83aef2ec4a8`。失败状态保存在独立数据库 `.test-data/live-6be76694-5ea4-4c18-bae9-bae199375d76.sqlite3`，核对报告数与证据快照数均为 0。以上没有调用 Tavily。

已针对失败明确要求英文来源的 quote 保留原语言、标点并逐字连续复制；中文要求仅适用于研究结论。引用/数值修复与结构修复共享一次预算，另允许一次临时故障重试，每次模型逻辑调用最多 3 个 HTTP 请求。持续无效引文或虚构图表仍失败；修正后也必须重新通过逐字引用、数值及模型语义审查。本轮模拟测试已覆盖修复路径，不能替代待执行的真实重跑。

后端完整回归采用 `PYTHON_DOTENV_DISABLED=1` 和临时数据库，不加载本机真实密钥。浏览器测试显式设定 `JUSTREAD_SEARCH_PROVIDER=auto`，避免继承本机 OpenRouter 设置后与 fixture 冲突；初始化文档使用 `cp -n` 保留已有配置。本轮未重新构建前端或运行 Docker。pytest 仍有一条 Starlette TestClient 弃用提示。

## 2026-10-07：默认宽松模式与成功的真实完整实验（A–D 扩展之前）

按用户要求取消研究业务的强制质量门槛：没有来源或摘录、引文/数字不匹配、审查不支持、固定子问题/章节数量不符，均不再直接阻断生成。内部模型允许默认空字段、null 和额外字段，兼容字段类型转换与 JSON 代码围栏；OpenAI/OpenRouter 请求使用 `strict=false`，OpenRouter 使用 `provider.require_parameters=false`。缺搜索配置时可基于模型知识生成，并注明未核实；主要模型调用无法完成、取消或超时仍不会伪报为成功。

报告的 `generation-notes` 展示待核实事项，内部快照保存 `validation.mode=relaxed` 与完整 warnings。未匹配的引用不被称作核验通过；未知来源编号不由后端追加。图表允许负数、单个数据点和明确的科学计数/千分位/百分数；无法明确解析、非有限、下溢或百分号与单位冲突的值只保留文字，不补为 0。冲突值保留首值并提示，多口径保留分组。超长内容缩减展示并提示，保留内部研究对象和来源章节，避免整份报告因显示预算而保存失败。

| 检查 | 本次结果 | 范围与限制 |
| --- | --- | --- |
| 完整后端回归 | **223 passed** | MockTransport/fixture；宽松生成、无来源/无搜索配置、补充步骤降级、主步骤错误、取消、保存、解析与超长内容边界 |
| 新增浏览器回归 | **4 passed** | desktop/mobile，真实本地报告导入 API + 明确模拟内容；负数单点、待核实说明、HTML 导出，以及 ±1e308 图表不溢出 |
| TypeScript 检查与生产构建 | **通过** | `vue-tsc -b`、`vite build`，使用 Node.js 24.19.0 |
| 当前 OpenAPI | **已更新并核对一致** | 8 个路径，图表有限负数与现有报告契约 |
| 真实 OpenRouter `--full` | **full_ok / succeeded** | 真实模型与 OpenRouter 检索，无 Tavily；业务 HTTP 使用进程内 TestClient，供应商走真实网络 |

真实问题仍为“Python asyncio 中 TaskGroup 与 gather 的异常传播和取消行为有什么区别？”，深度 `brief`。任务 ID：`83ac7857-eebf-4da9-8ff1-3a3ff0c93b8c`；报告 ID：`4f45e4c3-35c7-470c-ba9f-46647129c83f`。本次成功生成 **7 个章节、8 个来源、8 条研究结论、1 项材料缺口、8 项审查记录**，没有图表数据。内部记录含 **3 条提示**（2 条结论引文/数字未能自动核验，1 条遗漏结论自动编排）；没有阻止报告保存。

独立数据库为 `.test-data/live-63bf2328-e009-4407-8a2d-ab99a572847c.sqlite3`，核对报告和内部快照各 1 条，公开响应不含 `_evidence` 或来源原文。实测结果的静态副本和公开 Report JSON 保存于 `../output/JustREAD_本地实测_20261007/`。真实任务成功表示调用、生成、读取和保存已连通，**不表示研究结论通过人工质量验收**。

本次全套后端测试禁用 dotenv 并使用临时数据库；浏览器测试使用 fixture，不调用付费模型。未重复跑历史全部 30 项浏览器测试，未运行 Docker，未上传 GitHub。pytest 仍保留一条既有 Starlette/httpx TestClient 弃用提示。静态实测副本未通过内置浏览器自动预览（工具禁止 `file:` 协议），未采用替代浏览器方式绕过此限制。

## 2026-10-07：A–D 全流程扩展（补Key前的验证）

| 检查 | 最新结果 | 证据边界 |
| --- | --- | --- |
| 全套后端 | **299 passed，7.12秒** | MockTransport/fixture、临时库，禁用dotenv，无真实Key |
| 全套桌面/手机浏览器 | **42 passed，2.0分钟** | 隔离8001/5187，实际业务API与明确fixture/mock，无付费调用 |
| TypeScript与生产构建 | **通过** | vue-tsc -b、vite build（3.38秒） |
| OpenAPI一致性 | **通过** | API1.0.0，19路径/21操作，JSON与当前代码一致 |
| 自包含离线阅读器 | **通过** | 0 pageErrors/0外部请求；2流程、1关系、真实折线与表格、三种WebGL网格、爆炸/部件选择、正文/证据哈希 |
| ZIP文件级完整性 | **通过** | 当前模拟包8个文件全部SHA-256及长度匹配；损坏包检测有独立测试 |
| 本机扫描PDF OCR | **通过** | Apple Vision实际识别中英文模拟扫描页与20/30/50，页码=1；不代表数据集准确率 |
| 恢复后的8000服务 | **通过健康探针** | worker正常；未读取到模型Key，ready=false，与进程健康区分 |
| Docker/远程CI/本轮真实模型 | **未执行** | Docker本机未安装；当前Key缺失；不以配置或模拟测试冒充真实联调 |
| GitHub上传 | **未完成** | 非交互Git读取远端失败，命令行未认证；没有强推或覆盖远端 |

新增后端证据：原件/分片和所有权；真实文字PDF/DOCX解析；OCR失败保留原件且明确提示；上传参与分析；子问题关联、来源定位、数值核算回流、冲突保留；checkpoint同输入复用、输入变化失效与阶段失败恢复；整份报告AI审查、单轮表达修订、审查不可用不伪称完成；双worker中旧协程吞取消后返报告或抛错均被执行批次隔离；版本并发冲突、恢复建立新版本、分享公开投影固定且可撤销；审查版本分离、导出审查快照缓存刷新；图文/场景/阅读顺序绑定提示及资产哈希。

浏览器新增流程：材料上传和研究规格、A–D报告工作区、证据定位、五类图解、三维mesh/旋转/爆炸/编辑/JSON包导入、主动核算按选入顺序生成新版本、AI/人工审查、版本比较/恢复、无Cookie guest冻结分享及撤销、私有原件不随分享披露、分享页零私有来源API请求。旧阅读和刷新恢复用例全部重新通过。

最新前端实际截图：[桌面三维工作区](verification/workspace-desktop.png)、[手机三维工作区](verification/workspace-mobile.png)。画面内容均为明确模拟的验收数据。当前用户8000页面已刷新并确认显示目标读者、范围、深度、研究材料库和概念三维入口；没有创建虚构的真实报告。

离线验证使用新的明确模拟夹具，仅验证当前离线阅读器生成的代码，不涉及历史被内置浏览器拒绝的file页面。可复现：

```sh
node scripts/test-offline-reader.cjs docs/verification/offline-fixture.html
```

脚本启动独立headless Chrome，用内存路由提供模拟包页面，所有其他请求拦截；不使用真实服务或模型。[结果JSON](verification/offline-qa-result.json)、[截图](verification/offline-qa.png)、[模拟ZIP](verification/offline-fixture.zip)已保存。原件OCR试验在`.test-data/ocr-simulated.pdf`，不是用户研究数据。

pytest保留一条既有Starlette/httpx弃用提示。真实OpenRouter新的A–D联调仍需本机补Key，再确认实际来源、计算请求、三维适用性及最终报告质量；此前成功的brief实验仅是旧版历史证据。

## 2026-10-07：补Key后的真实 A–D 联调（最新）

提供方为OpenRouter，模型`openai/gpt-6-luna`，模型与搜索共用本机Key；未使用Tavily。模型小型结构化探测通过。业务接口使用FastAPI TestClient，供应商模型和检索请求走真实网络；完整生成使用独立数据库，没有替换日常工作空间。接口仍为非流式请求与轮询。

| 检查 | 结果 | 实际范围 |
| --- | --- | --- |
| 修复后全后端回归 | **313 passed，6.84秒** | MockTransport/fixture，禁用dotenv，不调用真实模型 |
| 真实A–D及交付接口 | **31/31通过，137.63秒** | 上传/下载/定位、自动与主动核算、三维、整篇及版本AI审查、人工记录、修改/409冲突/恢复、冻结分享/撤销、HTML/ZIP |
| 其中报告生成 | **120.62秒** | 6章节、8候选来源，其中6个HTTPS来源含实际检索摘录；20条候选引用；2自动计算；6概念部件 |
| 最终真实输出离线浏览器 | **27/27通过** | 0页面错误、0外部请求；搜索/目录/流程与关系节点、数据表、三维选择/旋转/缩放/爆炸/重置、浏览器SHA256 |
| ZIP完整性 | **通过** | 9个文件（8个被manifest校验）；verify.py通过，篡改report.json后失败，恢复原字节后保留正常包 |
| 8000本地服务 | **ready=true，worker健康** | 已重启读取新Key与单位修复，未输出Key |
| 本地HTTP与内置浏览器展示 | **通过** | 真实生成结果的导入副本，匿名冻结分享200；无会话私有读取428、其他会话404；A来源证据与C图解/两组指标/三维显示正常 |

输入材料是自建无刷直流电机概念描述，明确标注所有数值为模拟软件验收数据：A为20W/85%，B为30W/90%。首次真实生成发现百分比差值单位错误，已修正为**5个百分点**，相对变化率仍使用%。自动计算结果`50 W`和`5 百分点`均与独立算术检查一致，并通过`calculation_refs`进入研究结论；主动复算新建版本。测试共建立4个报告版本，旧v1保持不变。

最终任务ID：`620b3a71-eb19-48e3-8a1a-0f95a35ce663`；报告ID：`1ae1cb69-2cf6-4d85-965c-3a79cbe7b11b`。数据库与逐项结果：`.test-data/live-ad-da436d23-c09c-4cbc-87ef-3c538df9ed2b/`，包括`results.json`、`runs.json`、`offline-browser-qa.json`及固定v1 HTML/ZIP。用户副本在`../output/JustREAD_A-D真实联调_20261007/`。

首轮报告已实际应用1条表达修订；最终重跑的2条建议因改变数字或不确定性标记被拒，`auto_revision.applied=[]`，没有将其称作已修订。最终输出仍有**27条提示**，包括2条结论引用/数字、4条候选数据口径、1项审查分歧、重复表述及图文覆盖问题。算法及交付验收通过，不表示报告事实或商业质量已经验收。三维仅为归一化概念模型；部分标签重叠，部件选择和说明可读。本次未生成折线图，折线功能仍只有此前模拟浏览器证据；扫描PDF OCR、Docker、并发容量与长任务恢复也没有在本轮真实模型用例中重复执行。

首轮测试脚本在Cookie复制处遇到`AttributeError`（13项已有检查通过），已修为`httpx.Cookies`后完整重跑成功；该错误不是供应商认证失败。首轮真实快照另补36/36交付检查，其中认证采用明确TestClient fixture，不能称为鉴权端到端验证。最终31项流程使用正常TestClient Cookie；本地8000补验收使用实际HTTP与正常会话，未使用认证fixture。

重复完整真实验收（会产生上游费用）：

```sh
backend/.venv/bin/python scripts/test-live-ad.py --run
```

本轮只修改后端核算单位、针对测试、真实验收脚本及文档；前端未改，42项桌面/手机全套浏览器和TypeScript/Vite构建仍为前一阶段记录。Docker、GitHub上传和远端CI仍未完成。
