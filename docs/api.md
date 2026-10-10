# JustREAD A–D 开发接口

FastAPI + SQLite，接口前缀 `/api/v1`，UTF-8 JSON。成功直接返回对象；错误为 `{"error":{"code":"...","message":"..."}}`。调研和模型均非流式，浏览器轮询任务。详细字段见 `openapi.json`，运行时访问 `/docs`。

## 公共约定

- 首先 GET `/health` 建立 HttpOnly 会话 Cookie，业务请求使用同源 Cookie。材料、任务、报告按会话隔离；只有 `/shared/{token}` 可匿名阅读。
- 写操作检查同源 Origin。Key 仅放后端环境变量或项目 `.env.local`，前端不能传模型、密钥或代码。
- `ready` 只说明模型配置存在；`runtime.healthy` 说明 worker 运行状态。两者都不代表授权、额度、网络或报告事实已核验。
- 默认宽松生成，证据缺口、引用不匹配、AI 审查问题记为提示；模型鉴权失败、主调用协议失败、取消、超时不能伪报成功。
- 请求正文：普通接口 2 MiB，材料上传 30 MiB，保存版本 8 MiB；单材料原件最大 20 MiB。这些是资源边界，不是研究质量门槛。
- 常用错误：428 SESSION_REQUIRED；403 ORIGIN_REJECTED；404 SOURCE_NOT_FOUND/TASK_NOT_FOUND/REPORT_NOT_FOUND/VERSION_NOT_FOUND/SHARE_NOT_FOUND；409 VERSION_CONFLICT/TASK_STATE_CONFLICT/TASK_ALREADY_ACTIVE/IDEMPOTENCY_CONFLICT；413 超限；422 输入无效；503 提供方配置或上游不可用。

## A：目标、材料与证据

| 方法与路径 | 请求 | 返回 |
| --- | --- | --- |
| GET `/health` | 无 | `{status,ready,mode,version,runtime}`，建立或续期会话 |
| POST `/sources` | `{name,media_type?,content_base64}` | 201，完整 Source |
| GET `/sources` | 无 | `{sources:[摘要]}` |
| GET `/sources/{id}` | 无 | 原件信息和 chunks |
| GET `/sources/{id}/download` | 无 | 原始二进制附件 |
| POST `/research-tasks` | ResearchRequest，头 `Idempotency-Key: UUID` | 首次 201，重放 200，Task |

```json
{
  "question":"根据材料比较两种技术方案",
  "depth":"brief",
  "source_ids":["上传返回的资料ID"],
  "reader":"技术研发人员",
  "scope":"机制、成本与主要限制",
  "enable_3d":false
}
```

question 去除首尾空白后为 1–1000 个 UTF-16 单位；depth 为 brief/deep，默认 deep。其他默认分别为 `[]`、普通读者、空范围、false。source_ids 最多20项，必须属于当前会话。同键同规范化请求返回同任务，同键不同请求返回409。同会话最多一个排队或运行任务；网络断开不等于后台失败。

材料支持 PDF、DOCX、Markdown、TXT、CSV、JSON。原件、SHA-256 和解析片段一起保存；PDF 保留页码，无文字页尝试本地 OCR；DOCX 按段落定位，不推测 Word 页码。CSV/JSON 保存为可引用材料，不自动推定单位。OCR 无正文时保留原件并记录缺口，OCR 摘录需对照原页核实。

Source：`id,name,title,kind,hash,revision,createdAt,media_type,size_bytes,pages,chunks,warnings,extraction,text_characters`。chunk：`id,text,page,paragraph,start,end,method`；start/end 为该页或原段文字中的字符区间。网络来源另有 URL、访问时间和摘录类型。摘录匹配不等于事实核验。

研究材料参与调研；旧 POST `/reports/import` 是导入已有正文供阅读，不启动模型。

## A–D：执行与恢复

| 方法与路径 | 请求 | 返回 |
| --- | --- | --- |
| GET `/research-tasks/{id}` | 无 | Task |
| POST `/research-tasks/{id}/cancel` | 无 | 取消后或已终止 Task |
| GET `/research-tasks/{id}/runs` | 无 | `{runs:[执行记录]}` |
| POST `/research-tasks/{id}/actions` | `{action,from_stage?}` | Task |

action 为 pause/resume/retry。pause 接受 queued/running；resume 仅接受 paused，沿用任务ID、新建执行批次；旧执行不能写入或使新执行失败。retry 使用新任务ID，旧任务和报告保留。from_stage 为 A/B/C/D，仅复用该阶段之前、输入指纹一致的 checkpoint；省略时复用成功步骤，从未完成处继续。

Task：`{id,status,step,message,report,error,expiresAt}`。status 为 queued/running/paused/succeeded/failed/cancelled；step 为0–3，不是百分比，成功原子发布后才为3。succeeded 带完整Report，failed带`{code,message}`，其他状态不返回半份报告。

为保持旧阅读接口兼容，已完成task.report指向该报告当前版本；固定初始产物读取 `/reports/{id}/versions/1`。

阶段记录：A.plan/A.sources → B.analysis.Qn/B.review → C.layout/C.expression → D.quality_model/D.quality。用量记录保存模型、耗时及提供方可用的token/cost，不存Key、Cookie或完整请求。

每次执行默认总期限1800秒；暂停继续重新计时。重启时running转failed/SERVER_RESTARTED并保留checkpoint，可显式retry；queued继续，paused保留。终态任务与幂等记录默认保留24小时，报告版本独立保留。Cookie清除不删除数据，当前无账户恢复。

## B–C：研究与表达对象

Report保留旧字段 `id,source,title,question,category,summary,createdAt,minutes,chapters,chart,chartMeta,bookmarks`，增加`version`与`workflow`。旧报告导入后建立v1，旧阅读和书签接口兼容。

`workflow.schema_version="1.0"`：

| 字段 | 内容 |
| --- | --- |
| research_spec | 问题、深度、读者、范围、材料ID、三维开关 |
| sources / evidence | 同批来源和片段；quote/source_id/chunk_id/locator/verification/statement_kind |
| plan / research_ir | 子问题、答案、claims、coverage、conflicts、unknowns、relations |
| datasets | 指标/单位/时期，数据行`{id,label,value,value_text,source_id}` |
| calculations | 运算、原输入、公式、Decimal结果、单位、来源和状态 |
| reading_plan / narrative_blocks | 章节顺序、阅读目的、前置知识、论证和证据关联 |
| visuals | flow/relation/bar/line/table；节点边或labels/series，单位、时期、来源 |
| scene | 可选三维parts/relations/annotations/unknowns/provenance |
| ai_quality_review / auto_revision | 整篇AI审查、单轮表达修订、before/after/reason、审查版本号 |
| validation / quality_review / manifest | 研究提示、程序检查、固定版本哈希 |

三维为可编辑概念示意：box/sphere/cylinder部件，position/size为有限三维坐标；未知尺寸标`dimensions_known=false`。支持SceneSpec JSON包导入导出；离线包提供生成的GLB。当前不支持任意CAD/GLB导入、物理仿真或逆向重建。

workflow.sources仅公开白名单元数据：chunks包含定位信息及quotes，excerpts包含实际引用quote/locator/verification。完整文字只通过会话鉴权的 `/sources/{id}` 获取；selected范围表示本次送模型的片段，不能解释为整份原件已经被阅读。

自动核算引用已提取数据，结果回流到论证；失败记录原因，不造数值。主动复算复用报告actions：

```json
{"action":"calculate","base_version":1,"operation":"difference","input_refs":["D1","D2"]}
```

返回`{calculation,report}`，建立新版本。支持sum/mean/min/max/difference/ratio/percent_change；difference及后二项需两个输入、单位一致。difference=第二−第一；ratio=第一/第二；percent_change=(第二−第一)/abs(第一)×100。不能执行自由代码或eval。不可计算返回422 CALCULATION_UNAVAILABLE，不建版本。核算正确不证明输入或跨时期比较有意义。

百分比输入的绝对差值使用`unit="百分点"`：85%到90%的difference为5个百分点；percent_change为约5.88235%，表示相对增长。sum/mean/min/max保留输入单位，inputs中的原始单位不改写。

## C–D：报告、版本和审查

| 方法与路径 | 请求 | 返回 |
| --- | --- | --- |
| GET `/reports?q=关键词` | 可选文本筛选 | `{reports:[Report]}` |
| GET `/reports/{id}` | 无 | 最新Report |
| POST `/reports/import` | 完整Report | 重新分配身份的v1，不调用模型 |
| PUT `/reports/{id}/bookmarks` | `{bookmarks:[chapter_id]}` | 最新Report；不修改固定正文 |
| GET `/reports/{id}/workflow?version=n` | 默认最新 | workflow及该版本独立reviews |
| GET `/reports/{id}/versions` | 无 | `{versions:[{version,createdAt,title,diff}]}` |
| GET `/reports/{id}/versions/{n}` | 无 | 固定Report |
| POST `/reports/{id}/versions` | `{base_version,report}` | 新版本Report |
| POST `/reports/{id}/actions` | 下表动作 | 按动作返回 |

base_version必须等于服务器当前版本，否则409 VERSION_CONFLICT；前端重新加载后合并。服务器固定身份、来源、所有者及时间。历史正文、证据与资产清单不覆盖，恢复历史内容同样新建版本。人工修改不自动继承旧版本AI或事实审查结论。

| action | 附加字段 | 返回 |
| --- | --- | --- |
| validate | `version?` | kind=automatic程序检查记录 |
| review | `version?` | kind=ai实际模型审查；fixture标simulated=true |
| human-review | `version?,reviewer,decision,notes` | kind=human人工记录 |
| restore | `version,base_version` | 从历史内容新建Report |
| calculate | `base_version,operation,input_refs` | `{calculation,report}` |
| publish | `version?,ttl_seconds?` | `{token,report_id,version,expiresAt,url}` |
| revoke-share | `token` | `{revoked:true}` |

version默认最新。分享默认7天，允许60秒至365天；当前前端创建24小时链接。AI审查可能等待多次完整响应，前端采用240秒超时。自动、AI、人工记录分开，互不替代。

交付前AI整篇审查允许一轮有限表达修订并保留before/after/reason，随后重新执行程序检查。未执行、无审查结果、旧版本审查明确区分；可选审查失败转提示，鉴权失败终止。缺口与未知仍需人工判断。

## D：固定交付

| 方法与路径 | 返回 |
| --- | --- |
| GET `/reports/{id}/export?version=n&format=zip` | 自包含ZIP附件 |
| GET `/reports/{id}/export?version=n&format=html` | 自包含HTML附件 |
| GET `/shared/{token}` | 无需Cookie，固定Report；过期/撤销404 |

version默认最新；分享URL为`/#/share/{token}`。修改、恢复或重生成不改变旧链接，个人书签不披露。公开投影仅包含来源元数据及已引用摘录，未引用的原件正文不随分享公开；完整内部提供方对象不通过公共API返回。分享manifest标明delivery_scope=public，并分别记录公开正文哈希和原版本正文哈希。

ZIP含index.html/report.json/evidence.json/reviews.json/manifest.json/verify.py，含三维时另有scene.glb。HTML无需远程CDN或运行时网络，支持搜索、图解、图表、三维旋转/缩放/爆炸/部件选择。reviews冻结导出时该版本记录，新增审查需重新导出。

报告正文哈希排除bookmarks和manifest自身，证据另有哈希；包清单记录文件长度与SHA-256，不包含自身。解压运行`python3 verify.py`核对完整性。哈希通过只证明版本未改变，不证明事实正确。

## 服务端大模型API

前端只调业务接口。后端固定供应商和模型，从环境变量读取Key；支持OpenAI Responses、Anthropic Messages、OpenRouter Chat Completions。请求`stream=false`、结构化JSON；OpenRouter默认检索共用同一个Key。单次调用允许一次临时故障重试和一次格式修复，受任务总期限约束。

ResearchRequest → Plan → Sources/Chunks → Analysis/Calculations/Review → ResearchIR → Layout/SceneSpec → FinalQuality → Report/Manifest。上传材料与网页文字都是研究数据，不能替代用户指令。用量是提供方返回记录，缺失保持缺失，不能据此声称实测节省成本。
