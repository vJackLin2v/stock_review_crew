# 项目进度存档（2026-08-03 11:50 需求确认）

## 状态
- ✅ 需求确认：`docs/requirements.md`（v1，Q1-Q10 全部确认，6 模式）
- ✅ 环境核查：Python 3.12.11（.venv）；Node 22；F:\tdx 通达信本地数据可用；
  gh CLI 未装；**网络根因已定位**：沙箱注入假代理 `http://127.0.0.1:9`，
  显式指定 `http://127.0.0.1:7890` 后 git/python 均可联网（PLAYBOOK §1）
- ✅ GitHub：仓库已验证（public / main / LICENSE 2711d1f）；基线已推送
  （合并提交 `222bacb`）；**7 个 issue 已创建 #6-#12**
  （#6 I1 / #7 I2 / #8 I3 / #9 I4 / #10 I5 / #11 I6 / #12 R1）
- ✅ Wave 1 全部完成：I1 数据层（67 tests）+ I2 复盘引擎（32）+ I3 聊天引擎（25）
  + I4 历史存储（50）+ I6 前端（build/normalize/冒烟通过）
- ✅ Wave 2 完成：I5 后端（16 tests）+ 集成修复（全量 **190 passed**，进程干净退出）
- ✅ 集成修复：py_mini_racer 禁用（Windows IOCP 退出死锁）、posthog 遥测禁用、
  chroma 降级兜底（PanicException 捕获）、东财 HTTPS→HTTP 改写+多子域轮换、
  config 兼容 DEEPSEEK_BASE_URL、.env 修复（BOM/拼接残留/换新 Tushare token/
  TDX_PATH）、.venv 补齐 fastapi/pytest（uv sync）
- ✅ 真实数据冒烟：6 模式 + 聊天 + 窗口校验（详见 docs/SMOKE.md）
- ✅ Wave 3：R1 业务审计完成（docs/audit-report.md；S1-1 聊天白名单、S2-1 涨跌比展示、
  M1-2/M1-3/M1-4/M3 全部修复；复测 190 passed）
- ✅ 交付：git 历史重写为单提交 `7953229`（旧 token 已从历史移除）并推送
  stock_crew.git（main）；Cloudflare Tunnel 已部署（scripts/start_tunnel.ps1）
- ✅ R4 复盘链数据兜底（requirements-v2.md）：指数（东财push2his/腾讯日K备用）、
  分时（腾讯分时备用，仅当日、日期不匹配明确报错）、板块（腾讯行业备用）、
  资讯（新浪7x24 备用，仅主源为空时降级）；测试 204 passed；
  冒烟 2026-08-04 noon：指数/分时(腾讯,121bars)/板块(腾讯行业)/资讯全部有数据
  （此前「早盘复盘无数据」已消除）；实时快照并行化 ≤4s（9420540）
- ⏳ Wave 2：I5 后端 API + 集成审查
- ⏳ Wave 3：R1 业务逻辑审计 → 派发修复
- ⏳ 冒烟：6 模式真实数据（2026-08-03 午间窗口 noon；其余近期交易日补做）
- ⏳ 验收：全量 pytest、npm build、README、提交推送 stock_crew.git

> 2026-08-06 复盘降级/提示词泄漏修复 + Langfuse 启动打开（全量 288 passed）：
> 1. **提示词泄漏**：规则引擎 `_rule_analyst` 曾把 skill.prompt 原文写进分析 →
>    改为只展示 `skill.role` 人设一句话；
> 2. **一直降级三根因**：① langfuse CallbackHandler 每调用新建、回调故障拖垮 LLM →
>    config 单例 + `_invoke_llm` 回调失败去回调重试；② 空 DEEPSEEK_API_KEY 时
>    make_llm 抛 OpenAIError → config 导入崩溃 → 改返回 None 走规则引擎降级；
>    ③ 数据源降级为沙箱假代理环境性（用户环境无）；
> 3. **Langfuse 启动打开**：config 提供 langfuse_status()/langfuse_callback() 单例；
>    backend lifespan 启动日志 + /api/health 返回 langfuse 状态；启动脚本
>    （start_backend.ps1 / 启动服务.bat）启动前校验 .env LANGFUSE_* 并提示；
>    CallbackHandler 4.x 只收 public_key（签名核对）；
> 4. 实测：8502 health `langfuse.enabled=true/callback_ok=true`；真实完整复盘
>    5 分析师 LLM 全程正常、Langfuse trace 导出无错误（直连 cloud.langfuse.com）。
> 5. 待办：satori5.icu 功能清单 + AKShare 数据补充（需求确认后四线并行开发）。

> 2026-08-06 R10 完成（multi-agent-dev 五阶段流程，5 Agent 并行，一键回测 13/13 全绿）：
> **新功能**（docs/requirements-r10.md）：
> - F3 市场天气/情绪温度（P0）：规则引擎手算评分（base50+5项，clamp0-100），
>   首页天气卡（SVG 温度环+依据列表）；手算验收样例 100/晴/火热通过；
> - F6 纳斯达克（P0）：.IXIC/.NDX 近60日走势（新浪主源→东财备用，独立降级）；
> - F5 板块温度榜（P1）：hot/cold 对照+领涨股（东财 clist→realtime 链→stock_data 链）；
> - F2 额外指数（P2）：平均股价 880003（通达信协议实测可用）、微盘股 BK1158、
>   红利股 BK1641（东财板块实时）；AKShare 实测结论已冻结（PLAYBOOK §8 有探测记录）；
> - F4 风格坐标（P2）：大盘/小盘（沪深300 vs 中证1000）+ 价值/成长（红利股 vs 小盘成长）；
> - F9 观察列表（P1）：data/watchlist.json + 增删改排 API（409/404 中文）+ 前端面板；
> - F12 设置页（P2）：主题/红绿配色/默认图表周期/字号（localStorage）；
> - 前端样式参考 satori5.icu 卡片网格（TodayDataCards/SettingsView/WatchlistPanel）。
> **Q4 质量评估**（Langfuse 打分，硬需求）：
> - quality.py 新增 trace_review/score_review/score_chat + LANGFUSE_GUIDE.md（教学文档）；
> - 复盘 job result 附 quality 摘要（score/breakdown/passed），写回 Langfuse
>   review_quality/chat_quality score；真实复盘实测 5/5（numbers 置信度自检 3 分）；
> - 数字核验三处修复：代码正则（小数/千分位）、实体比率归一、rubric 置信度自检。
> **集成修复**（一键回测暴露）：
> - ChatView intent effect 依赖内联回调 → 创建会话竞态卡 loading（latest-ref 修复，
>   影响所有用户进入聊天，已修）；
> - 降级断言测试补全 mock（_sentiment_from_realtime/_zt_pool_from_ths/日K备用等），
>   regression.py pytest 子进程注入假代理 + 价格断言去写死（1328→数字存在性）；
> - 报告提示词单位铁律（成交额/成交量严禁混用，违反视为数据错误）。
> **测试**：全量 364 passed（基线 288 + R10 76）；verify:normalize 全绿；
> 一键回测 13/13（pytest+build+API 契约+聊天链路+桌面/手机浏览器冒烟）。

> 2026-08-06 二轮（用户反馈修复，全量 364 passed，一键回测 13/13）：
> 1. **复盘指数口径**：数据层 INDEX_CODES 去掉深证成指（报告/快照只显示 上证/
>    创业板/科创50，实测三行全数据）；index 块渲染 cap 40→160（科创50 不再「无数据」）；
>    报告 prompt 冻结指数表格格式（+0.57% 无括号小数、缺失「无数据」）；
> 2. **涨跌分布图表**：后端快照新增 breadth（distribution 9 档），ReportView 新增
>    BreadthChart 柱状图（红涨绿跌）；normalize/mock/契约断言同步；
> 3. **聊天近5日日K**：_build_context 对 stock_info 特化（recent5 完整保留，
>    修复 1600 字符截断切掉尾部日K）；实测上下文含 recent5；
> 4. **首页布局**：今日数据整行置顶 → 表单|聊天 → 五卡整行 → 观察|历史（grid-areas），
>    消除表单列 714px 空白；表单/聊天卡等高（flex:1 + 按钮底部对齐，实测 565=565）；
> 5. **市场天气严格化**：加分减半+上限±12（强市 79 分不再 100，弱市 10 分冰点）；
> 6. **Langfuse 捕获修复**（三个根因：get_client 未注册→disabled client、
>    OTLP 导出读代理静默失败→清理+NO_PROXY、未 flush 不导出→写回后 flush）；
>    实测复盘 13 条 ChatOpenAI trace + review_quality score 均上面板；
> 7. **首页今日数据指数卡**：只显示 上证/创业板/科创50（前端过滤）。

> 2026-08-07 手机端首页自适应（Playwright 390/360 实测无溢出）：
> - 根因：home-cols（观察列表/历史分组）grid 列 `1fr` 隐含 min=auto，被内容
>   撑到 604px（视口 390 横向溢出 230px）；
> - 修复：grid 列统一 `minmax(0,1fr)`（home-grid/home-cols/移动端单列）+ 子项
>   `min-width:0`；指数卡手机端 2 列紧凑（3 个 2+1）；卡片内边距 14px；hero 标题 22px；
> - 实测 390×844 与 360×780：scrollW=视口宽（无横向滚动）、无 JS 错误；
> - 同时修复服务启动 401 根因（config load_dotnet override=True，.env 唯一真源）
>   与聊天 401 中文提示（见 PLAYBOOK 2026-08-07 两条）。

> 2026-08-07 复盘报告手机端适配（Playwright 390/360 实测无溢出）：
> - 根因：① 报告 Markdown 宽表格（8 列）min-content 1255px 撑爆视口；
>   ② 降级说明里的超长文本（URL/无空格串）把卡片撑到 1285px（scrollW 1301）；
> - 修复：≤860px 下 .md table display:block + overflow-x:auto + nowrap（表格自身
>   横滚）；通用 min-width:0 + overflow-wrap:anywhere + body overflow-x:hidden；
> - 实测 390×844 / 360×780：scrollW=视口宽、表格可横滚、无 JS 错误、契约断言全绿。

> 2026-08-07 市场天气入复盘 + 风格坐标细分 + 指数卡涨跌家数 UI：
> 1. **市场天气纳入复盘数据与分析师 prompt**：fetch_data_node 用取数 blocks 调
>    compute_market_weather（复用首页规则引擎，不新增数据源）→ market_data 头部注入
>    「市场天气/情绪温度」区块 + snapshot.market_weather + state.market_weather 简报；
>    ANALYST_BASE_TEMPLATE 的「大盘判断」要求引用天气（R12）；规则引擎同步；
>    直调验证：blocks 正常时注入「晴（79 分/偏暖）」成功；数据源降级（zt_pool/
>    breadth/sentiment 全缺）时按规则标「无数据」不编造；
> 2. **风格坐标细分**：score 灵敏度 ×2（1pp=2 分），label 按差值分三档
>    （<0.5pp 均衡 / 0.5–1.5pp 略占优 / ≥1.5pp 明显占优）——红利 -0.73% vs
>    小盘成长 +1.30%（差 2.03pp）不再判「均衡」而是「成长明显占优」；
> 3. **指数卡涨跌家数 UI**：灰色长文本 pill → 红涨绿跌对比条（涨/跌双色数字 +
>     比例条，来源移入悬浮提示），实测比例条 43.4%/56.6% 正确、无 JS 错误。

## 关键文件
- 需求契约：docs/requirements.md（唯一真源）
- 草案存档：docs/requirements-draft.md；issue 文本：docs/issues.md

## Agent 负责人登记（模块 → agent，后续修改优先 resume 原负责人）
| 模块 | 负责人（昵称/id） | 状态 |
|---|---|---|
| I1 数据层 tools | Wegener（019fc5c7-f7c1-7133-aa84-710ee6b3493c） | ✅ 完成待命（67 tests；6 模式/降级链/竞价三源/tdx_local 只读/双接口） |
| I2 复盘引擎 graph/prompts | Laplace（019fc5c7-f871-7ba3-a157-90a59c72e280） | ✅ 完成待命（32 tests；6 模式感知/降级/进度钩子/免责声明；新增 host+review_assistant 角色） |
| I3 聊天引擎 chat/chats-storage | Arendt（019fc5c7-f95c-7743-8cfa-61d4b76161dc） | ✅ 完成待命（25 tests；单/多分析师交叉/免责声明/chroma chat_history） |
| I4 历史存储 reviews/导入脚本 | Lorentz（019fc5c7-fa62-7130-9b02-6688f5777258） | ✅ 完成待命（50 tests：存储 28 + 导入 22；真实 output/ 导入冒烟通过） |
| I5 后端 API | 待指派（Wave 2） | 待命 |
| I6 前端 frontend | Bernoulli（019fc5c7-fb4a-7ff2-bab5-d53910475a49） | ✅ 完成待命（build 通过；normalize 断言通过；headless 冒烟通过，快照子结构已固化供 I5 联调） |
| R1 业务审计 | 待指派（Wave 3，只读） | 待命 |
| R8-引擎 6 步流水线 chat.py | Anscombe（019fcbd5-48f3-7f32-bfc4-c5a6866469b3） | ✅ 完成待命（parse→need→fetch→analyze→summarize；取数失败终止；21 引擎测试） |
| R8-后端 SSE 透传 | Mendel（019fcbd5-49e0-7461-8d1f-9d7ab3566429） | ✅ 完成待命（15s keepalive + 30min 兜底；docstring 契约化） |
| R8-前端 阶段提示+流式 | Rawls（019fcbd5-4acf-75c2-b949-bc6a887207fb） | ✅ 完成待命（stage 提示/总结流式/240s 超时；build 通过） |
| R8-测试先行 验收用例 | Nietzsche（019fcbd5-4851-7a52-8f7e-16afc2f0dbfc） | ✅ 完成待命（8 验收用例，集成阶段启用后全绿） |
| R8-质量 可观测/数字核验 | Sagan（019fcbd5-4bbe-74c0-b3c1-bb5d05cd27ff） | ✅ 完成待命（quality.py：trace_chat/verify_numbers/rubric，25 测试） |

> 2026-08-04 补充模块：R1-R5 实时数据与兜底（requirements-v2.md）已完成并验收；
> 负责人沿用原模块（数据层 Wegener 已关闭，可 resume；本轮为集成直改+测试全绿）。

> 2026-08-04 R8 聊天智能流程（requirements-v4.md）：✅ 已完成并验收。
> 四线并行（引擎/后端/前端/测试先行/质量）5 Agent 交付；全量 261 passed（基线 207+54）；
> 验收 8/8（事件序列/parse 手算/白名单/取数失败终止/总结结构/多轮上下文/done 契约/无 LLM 回退）；
> 真实冒烟 2026-08-04：600519 双分析师 121.6s，stage 序列 parse→need→fetch→summarize，
> 总结流式 316 字，parse={trend,600519,近5日}，降级如实标注（分时→腾讯/央视无数据/板块→腾讯行业），
> 免责声明恒在；关键数字与腾讯实时行情逐位一致（1328.36/-2.25%）。质量评估 ≥4/5 通过。
> 已知工具级改进项：quality.verify_numbers 跨主体数字归属过严（可后续收紧）。

> 2026-08-04 稳定性与体验收尾（全量 265 passed，前端 build 通过，8501/8502 均已重启加载新代码）：
> 1. 聊天取数稳定性：数据键分级（CRITICAL_KEYS）——竞价/龙虎榜/资讯等非关键数据缺失时
>    降级标注继续回答，不再整轮终止；别名键归一（minute_data→minute_kline 等）避免重复取数；
>    need 提示词引导优先核心键（v4 Q9 附注）。
> 2. 复盘指数口径（requirements.md §4.1 更新）：创业板+科创50 必须保障，深证成指非必须；
>    缺失时指数块降级标注继续，R7 仅在必需指数缺失时终止复盘（新增 2 用例）。
> 3. 聊天显示模式（v4 Q8）：分析中显示「分析师名正在分析…」，完成后逐字展开分析简报+总结。
> 4. 移动端聊天：输入框改自动长高 textarea（16px 防 iOS 缩放/44px 触控）、会话列表横向滑动压缩。
> 5. 在途轮次完整性（v4 Q9）：切走/回历史不丢结果——前端模块级在途登记+轮询认领，
>    后端 worker 完整跑完并落盘；实测轮询 79s 接续成功（双分析师简报+总结齐全）。
> 7. 多会话并发（v4 Q10，2026-08-04 晚）：A 未走完可同时问 B——在途轮次改为
>    「按会话分桶」（rounds: {sessionId: liveState}，每会话独立 reveal 计时器），
>    输入框只锁当前会话、阶段提示/流式块按所属会话渲染，杜绝串显；
>    后端每轮独立 worker 线程天然并发（无共享状态），构建通过。
> 6. 隧道 502/进程崩溃：pyarrow arrow.dll 段错误（事件日志 Id 1000）→ 守护脚本
>    scripts/start_backend.ps1 自动重启（PLAYBOOK 已记录排查法）。
> 环境遗留：同花顺竞价 dataapi 404 死链（非反爬，浏览器方案也不适用——THS 无竞价网页）；
>   竞价可靠路径=开盘啦 Cookie（未配置）或东财 09:25 推算（当日东财 clist 502 临时失败）。
>   另：2026-08-04 17:53 起 opencode.ai LLM 端点经代理 SSL EOF（瞬态，PLAYBOOK 已记录）；
>   该时段聊天会如实提示「问题解析失败：Connection error」，非代码回归。

> 2026-08-04 深夜 R9 今日数据+个股详情 + ETF 修复（全量 285 passed，build 通过，双端已重启）：
> 1. **ETF（5xx）修复**：市场前缀映射漏 5xx → 588170 行情/分时全恢复（腾讯实时 0.865/+4.34%/
>    五档、分时 242 根、TDX 日K/1分钟K 正常）；通达信 ETF 快照价格 ×10 已归一（PLAYBOOK 记录）。
> 2. **今日数据板块**（requirements-v5.md Q1-Q5 已确认）：首页「实时数据」升级为「今日数据」，
>    指数卡加涨跌家数（通达信协议 enrichment，60s 缓存：上证 1395/892），个股行情卡加五档+详情入口。
> 3. **个股详情**：GET /api/data/stock/{code}（quote/日K60/分时/5分钟K/1分钟K/MA5-20，各块独立降级），
>    前端 StockDetail 视图（分时折线、日K蜡烛+MA 叠加、5分钟/1分钟K 蜡烛切换、五档面板、指标卡、免责声明）；
>    入口=首页搜索框「查看详情」+ 聊天页「📊 详情」+ 行情卡详情按钮。
> 4. 冒烟：600519 详情五块齐全（1328.36/60日K/242分时/240×5m/800×1m/MA5=1344.14）；ETF 详情正常；
>    realtime 指数带涨跌家数；契约/手算用例 11+1 条新增。
> 5. 前端点击冒烟补课（用户反馈「查看详情报错」）：根因=App.jsx 给 HomeView 漏传
>    onOpenStockDetail（TypeError）——已修复；并用 Playwright 无头 Edge 真实点击冒烟
>    8/8 通过（首页详情/ETF 详情/行情卡详情、无 JS 错误）；playwright 已入项目依赖
>    （此前被 uv sync 清掉，PLAYBOOK 已记录「只装不进锁」教训）。

> 2026-08-04 深夜二轮（全量 288 passed；代理 7890 故障期间实测直连兜底）：
> 1. **代理直连兜底**：代理客户端未启动导致全板块降级——realtime/stock_data/ths_crawler
>    全部 HTTP 请求封装 `_get`：ProxyError 自动直连重试（国内行情源直连可用）；
>    browser_crawler 启动前探测 7890，未运行不传代理。实测代理全挂时 realtime 0 降级。
> 2. **手机版聊天**：会话列表移除（display:none），聊天内容全屏；切换会话走侧栏抽屉
>    （☰ → 对话记录），无头 390×844 冒烟通过。
> 3. **成交额 NaN/缺失**：腾讯行情补解析成交额（parts[37] 万元→元）；realtime/tdx_remote
>    _to_float 拒绝 NaN/Inf；前端 normalize nullable NaN→null。实测 600519 成交额
>    5,004,070,000 元、ETF 588170 9,584,470,000 元。
> 说明：LLM（opencode.ai）仍需代理，代理挂时聊天按设计报 Connection error（PLAYBOOK 已记录）。

> 2026-08-04 晚 数据源突破：**通达信协议直连（tdxpy）已接入**（全量 273 passed）。
> 背景：扫描 167 台主站，22 台返回真实数据（其余为新版认证哑数据）；pytdx 停更换 tdxpy。
> 接入点：个股实时行情/个股日K/分钟线/指数日K 四条降级链（requirements.md §4.1 已更新）。
> 实测能力：实时五档✅ 日K✅ 指数K（含涨跌家数）✅ 1/5/15/30/60分钟K（约6个交易日）✅；
>   历史分钟补做实测通过（07-31 分钟 240 根）；解决本地 F:\tdx 陈旧（2025-05）与东财 502 依赖。
> 仍未覆盖：分时曲线/逐笔/竞价段（新主站协议格式变更）；竞价仍靠 开盘啦 Cookie 或东财 09:25，
>   同花顺客户端抓包发现接口为下一候选（需用户配合 Fiddler）。

> 策略：模块负责人任务完成后不立即关闭；同类修改优先 resume；仅当确定不再改动或
> 并发名额不足时才回收。

## 冒烟计划（6 模式，每条记录日期/模式/数据源与降级/输出摘要）
| 模式 | 计划日期 | 说明 |
|---|---|---|
| noon | 2026-08-03（今日 11:30-13:00 窗口） | 实时数据 |
| pre_market | 近期交易日补做（如 2026-07-31） | 外盘/资讯标注仅当日缺失 |
| auction | 近期交易日补做 | 竞价数据按降级链 |
| intraday_am / intraday_pm | 窗口内执行或补做标注 | 分时历史深度实测 |
| close | 2026-08-03 15:00 后 | 实时数据 |

## 阻塞项
- ✅ 已解决：GitHub 连接器 403 → 用 PAT 走 api.github.com（python urllib + 代理）建 issue 成功
- ✅ 已解决：远端仓库确认存在（public），基线已推送（222bacb）
- ✅ 已解决：网络 → 显式代理 127.0.0.1:7890（沙箱假代理 127.0.0.1:9 需覆盖）
- ✅ 已解决：8502 旧代码实例未重启 → 用户看到旧聊天行为（无 parse/needs/stage、定性回复）；
  已停旧实例（15:53 启动）并以新代码重启 8502，行为探针通过（stage 四步 + 天齐 002466 真实行情返回）；
  PLAYBOOK 新增「旧服务实例未重启 / 多实例并存」排查条目（含版本行为探针法）
- ⏳ Wave 1 进行中：I1 数据层 / I2 复盘引擎 / I3 聊天引擎 / I6 前端（4 个运行中）
- ⏳ 遗留：仓库根目录存在 agent 测试产生的临时目录（pt_*/sdc_*/sr_*/tmp* 等），集成阶段清理
- ✅ 遗留已清理（探针目录已删；剩余已知环境性降级见 docs/SMOKE.md「环境性降级说明」）

> 2026-08-07 Wave 1 冗余清理与文档归档（Agent D：P1-AB / P1-AA(api) / P1-AC / P1-AD，全量 366 passed）：
> 1. **P1-AB 删除一次性脚本**：scripts/rerun_7.py、scripts/batch_review.py；删除前 rg 全仓零引用
>    （仅 optimization-plan 计划文档自身提及）；删除后全量测试仍 366 全绿，证明无隐藏引用；
> 2. **P1-AA(api) 删除死导出**：frontend/src/api.js 的 sendChatMessage（非流式旧端点封装，无组件调用，
>    ChatView 走 streamChatMessage）；删除后 rg 零引用；后端非流式端点保留不动（不在本 Agent 写权限）；
> 3. **P1-AC 文档归档**：新建 docs/archive/，移入 requirements-draft/v2/v3/v5、langgraph-build-guide、
>    research-数据源调研、REALTIME 共 7 份；docs/ 根目录仅保留 requirements.md（唯一真源）、
>    requirements-v4/r10（代码/测试仍引用）、PROGRESS/PLAYBOOK/SMOKE/audit-report/issues/LANGFUSE_GUIDE/
>    optimization-plan-2026-08-07 共 10 份；
> 4. **P1-AD README 修订**：端口 8502→8501（与 AGENTS.md 统一）；测试命令改为全量
>    `python -m pytest -p no:cacheprovider -q`，实测 **366 passed**（2026-08-07，系统 Python 3.13，
>    无环境性失败）；架构图补 R10 模块（quality.py / tools/market_extra.py / storage/watchlist.py）；
> 5. ⏳ 待主 Agent 跟进：启动脚本（启动服务.bat / scripts/start_backend.ps1 / start_tunnel.ps1 /
>    regression.py / smoke_review.py）仍写 8502 端口，与 AGENTS.md 8501 不一致（脚本不在 Agent D 写权限内）。

> 2026-08-07 Wave 1 集成闸门（主 Agent 汇总，A/B/C 三列修复 + 集成验收）：
> 1. **A 列（复盘引擎/提示词，Ptolemy 报错前已完成落盘 + 主 Agent 复核）**：
>    P0-1 6 模式差异化提示词注入（本模式分析要点 + 三段章节随模式变化，pre_market 不再写「明日」）；
>    P1-A 单位铁律分层（表格原样引用 vs 正文亿元）；P1-B 报告 ≤1500 字/禁复制原文/禁套话；
>    P1-G 报告注入昨日/更早复盘对照；P1-C 规则主持人按立场标签判分歧 + 降级最多 1 轮辩论；
>    P1-D 辩论轮内 ThreadPoolExecutor 并行；P1-E 跌停板嵌套取数（dieting 块 + _rule_facts）；
>    P1-F close 关键闸门去掉 minute、intraday_pm 删除龙虎榜块声明（盘中本无数据）。
> 2. **B 列（数据契约/展示，Epicurus 报错前已完成落盘 + 主 Agent 复核）**：
>    P0-2 分布键名统一为含 % 规范键 + normalize 兼容旧键 + verify-normalize 键名校验；
>    P1-H 分布降级 note 透传 + 空态「数据缺失」；P1-I 首页 None→「—」（null 判断 helper）；
>    P1-J 情绪图负向指标用中性色 + tooltip 注明；P1-K sectors_bottom 跌幅榜透传；
>    P1-L 梯队 tooltip 龙头名单；P1-M fmtAmount 智能缩写 + 五档「手」；P1-N 双标题修复；
>    P1-O 首页删除内联确认；P1-P mock 涨跌家数比「1.56 : 1」；P1-AA 死导出清理。
> 3. **C 列（前端 UI/交互，Volta 报错前已完成落盘 + 主 Agent 复核）**：
>    P0-3 侧栏重试接线（onRetry: loadHistory）；P1-Q 后台任务仅在看进度/同报告时替换；
>    P1-R 报告目录/锚点/返回顶部；P1-S 复制 Markdown/纯文本（报告+聊天气泡）；
>    P1-T 会话切换控件 + 860/760 断点对齐；P1-U sessionsLoading 真实状态；P1-V 创建会话重试；
>    P1-W 同日报告切换条；P1-X 观察列表移除 📌 半成品展示；P1-Y Markdown 表格丢首行修复；
>    P1-Z aria-label + favicon + theme-color；P1-AA .new-icon 死样式清理。
> 4. **集成验收**：全量 pytest **375 passed**（系统 Python 3.13，沙箱假代理确定性环境）；
>    `npm run build` 成功（605 modules / 4.13s）；`verify:normalize` 全绿；
>    `scripts/regression.py` 一键回测 **12/13 PASS**（API 契约、聊天链路 done+落盘、浏览器桌面/手机冒烟
>    全部通过）；唯一 FAIL 为环境性：test_data_degrade.py::test_cache_used_when_sources_fail 在提权
>    真实网络下东财直连可达 → 缓存降级断言不触发（沙箱内该测试通过；regression.py 注释已预告此限制），
>    非代码回归；期间 .venv 后端两次被 pyarrow arrow.dll 段错误击穿（PLAYBOOK §8 已知问题）。
> 5. 已停用主 Agent 临时 8502 实例（避免多实例并存）；README 测试数更新为 375。
> 6. ⏳ 待确认/后续：启动脚本端口 8501/8502 不一致（README/AGENTS 8501 vs 脚本 8502）；
>    待确认项见 docs/optimization-plan-2026-08-07.md §二（情绪周期/主线梯队章节、聊天交叉表态、
>    板块聊天取数、双轨降级链合并、旧 Streamlit 入口岛、quality 展示、中文嵌入模型、荐股程序校验）。

> 2026-08-07 降级原因中文化修复（用户反馈「微盘股/高股息 通达信指数: time data 'i' does not match」）：
> 1. **根因**：① BK 板块代码（BK1158/BK1641）的降级链错误地调用 akshare 通达信日线
>    （stock_zh_index_daily_tx 对无效板块代码解析日期抛 ValueError，英文原文透传）；② 所有
>    兜底链 `errors.append(f"...: {exc}")` 直接把英文异常原文（502 Bad Gateway for url: ...、
>    time data 'i' does not match ...）拼进 degraded_reason 直达前端横幅，违反「错误信息一律中文」。
> 2. **修复**（market_extra.py / realtime.py）：
>    - BK 系（东财板块）改为单一数据源，失败即中文标注「{指数名}（{code}）：东财数据源请求失败
>      （HTTP 502）」，删除无意义的 akshare 通达信兜底；
>    - 新增 `_zh_source_error`：DataSourceError 透传（已中文）、中文异常消息保留、含英文原文
>      （连续字母）归一为「XX数据源异常」；覆盖市场天气/纳指/板块热度/风格坐标全部兜底拼接点；
>    - `_em_http_json` 对外只报「东财数据源请求失败（HTTP {code}）」，不再带 URL/英文；
>    - realtime 涨跌家数降级原因同样中文化。
> 3. **冒烟盲区修复**（scripts/regression.py 新增 2 项断言）：API 层检查 extra-indices
>    degraded_reason 无英文特征（time data/Bad Gateway/Traceback/url: 等）；浏览器层检查页面
>    body 无英文异常透传。旧代码实例（15:18 启动，未加载修复）被新断言当场抓出（10048 抢占端口）。
> 4. **环境教训**：修复验证必须确保 8502 实例加载的是新代码（行为探针：直接 curl
>    /api/data/extra-indices 看 degraded_reason 是否为中文）；守护脚本 start_backend.ps1 是
>    唯一推荐启动方式（自动清理旧实例）。已用守护脚本重启 8502（新代码）。
> 5. **验证**：全量 pytest **377 passed**；一键回测 **15/15 全绿**（含 2 项新断言）；
>    真实网络 502 场景下接口返回「微盘股（BK1158）：东财数据源请求失败（HTTP 502）」，
>    平均股价（通达信协议）正常出数。

> 2026-08-07 端口统一 8501（遗留项闭环）：
> 1. **统一口径**：以 AGENTS.md 为准，全部可执行入口 8502 → 8501：scripts/start_backend.ps1
>    （守护脚本，UTF-8 BOM+CRLF 重写）、scripts/start_tunnel.ps1（隧道指向 8501）、
>    scripts/regression.py（BASE 8501）、scripts/smoke_review.py（默认 8501）、启动服务.bat
>    （GBK+CRLF 保持原编码）、docs/SMOKE.md；README/AGENTS 原已 8501。
> 2. **停旧服务**：清理全部 start_backend.ps1 守护进程（含旧 8502 实例与重复守护，
>    父进程为 codex.exe 的会话托管实例）与 8501/8502 监听；确认双端口空闲。
> 3. **重启**：由新守护脚本拉起 8501（PID 24428）；8502 保持空闲。
> 4. **验证**：一键回测 **15/15 全绿**（后端健康 8501、API/聊天/浏览器冒烟全过）；
>    额外指数降级原因保持中文（东财 502 故障期间）。
> 5. 经验：杀守护进程时可能反复出现新实例（会话托管/自动化拉起源），以「最终端口唯一、
>    守护脚本为唯一启动入口」为准绳，不陷入追杀循环。

> 2026-08-08 R11 交付（multi-agent-dev 五线并行：4 开发 + 1 测试先行；需求见
> docs/requirements-r11.md，全部 Q1-Q10 定案）：
> 1. **T1 前端 Tabs（Huygens）**：首页 4 Tab（数据展示默认首个 | 当日复盘 | 分析师聊天 |
>    交割单分析），display:none 保持挂载（聊天在途不丢）；自动复盘检测（status 接口→自动
>    POST→进度轮询，sessionStorage 当天防重）；R3 降级/来源文字移入 tooltip；R5 目录 sticky
>    （桌面右侧/移动端顶部+滚动高亮）；R10 sessionStorage 在途标记恢复；侧栏「补做复盘」入口。
> 2. **T2 自动复盘后端（Mendel）**：每日一份存储（data/reviews/{date}/）+ 覆盖前归档
>    output/archive/{date}_{HHMMSS}/ + 旧多份结构读取兼容；GET /api/reviews/status（7 字段，
>    周一 15:00 前=上周五等 9 种边界表驱动测试）；POST 409 去重（已有记录/进行中 job）；
>    record_id={date}；import_output_history 兼容新结构。
> 3. **T3 聊天搜索流程（Meitner）**：R9 搜索→LLM 简报→注入分析师（tools/search.py 垂直聚合：
>    东财公告+新浪7x24过滤+博查可选开关默认关；ok=False 降级不阻塞）；R10 后端 pending_round
>    + worker 异常落盘「回复中断」说明消息；R6 表述铁律（禁「近乎腰斩」类比喻，数据+百分点）；
>    R8 快讯 A 股白名单过滤；quality 敏感词检测（P2 未接入评分）。
> 4. **T4 交割单分析（Locke）**：trades/ 包移植 synalysis parser/metrics/analyst（口径冻结，
>    65 测试全绿）；backend/trades_routes.py（upload/GET/analyze，中文错误、10MB、脱敏）；
>    TradesTab 前端；主 Agent 集成时 include_router 注册。
> 5. **集成闸门（主 Agent）**：全量 pytest **491 passed**；npm build 成功；verify-normalize 全绿；
>    真实网络验证 status（周六→最近交易日 8-07 ✓）、搜索模块（哈药股份 13 条公告含 8-04
>    集采中选 ✓）、交割单上传（4 条解析+1808 字报告+免责声明 ✓）；一键回测 **16/16 全绿**
>    （新增自动复盘 status 断言；浏览器段适配 Tabs 新流程：查询→行情卡→详情、手机先切聊天 tab）。
> 6. 遗留/说明：测试先行 Agent 超时未交付（已关停，验收由各模块测试+集成断言覆盖）；
>    trades_routes 的 include_router 在主 Agent 集成时补齐；博查 Key 保留未启用（实测质量差）。

> 2026-08-08 R12 交付（分析师模拟账户系统；multi-agent-dev：A1/A2 引擎+集成、
> A3 前端、A4 回测脚本、测试先行；Dirac 超时由主 Agent 接管实现）：
> 1. **账户引擎**（src/stock_review_crew/accounts/engine.py）：每分析师初始 10 万动态开户；
>    place_order(acct, code, name, side, target_price, position_cheng, day) 严格校验：
>    T+1（frozen_qty 冻结/跨日结算）、涨跌停（主板±10%/创业板科创板±20%，
>    Decimal ROUND_HALF_UP）、目标价≥涨停拒买/≤跌停拒卖、当日区间未触及拒、
>    100 股整数倍、费用（佣金万2.5最低5元/印花税卖出0.05%/过户费0.001%）、
>    超资金拒绝且拒绝完整记录（合规审计）；手算用例 13 项全绿
>    （2900 股折算→现金 70,992.46→次日卖出 101,419.318 对齐验收冻结数字）。
> 2. **复盘集成**（graph.py/prompts.py）：place_order 闭包工具绑定 LLM（行情取
>    fetch_stock_info quote/daily）；分析师 prompt 注入账户状态+A股规则铁律区块；
>    规则引擎不下单；报告 meta.accounts 附加字段（正文不变）；后端
>    GET /api/accounts、/api/accounts/{skill_id}（5 位分析师，host 等非交易角色已过滤）。
> 3. **前端**（Hooke）：报告快照「模拟账户」区块（资产汇总+单人展开曲线/交易表）、
>    侧栏「模拟账户」独立页（列表→详情：资产曲线/持仓/T+1 冻结标注/交易记录含拒绝）、
>    请求优先 mock 仅离线兜底。
> 4. **回测**（Arendt + 主 Agent 适配）：scripts/backtest_accounts.py 逐日回放 +
>    合规报告 + 断言清单（现金≥0/T+1/涨跌停区间/整百/费用）；引擎适配增加
>    position_cheng 签名分支（EngineAdapter + quotes 注入 + TradeResult 归一化）；
>    `--fixture` 确定性行情模式（真实数据源东财 502 + 历史板块不可用导致 0 交易时，
>    用低价股样例行情驱动全链路）。
> 5. **回测结果**：真实模式 18 天 0 成交（行情缺失，如实标注）；fixture 模式 5 人各
>    29 笔成交、0 违规、期末 110,575（+10.58%）、**引擎漏洞 0**——「不遵守规则/
>    超资金/无视涨跌停」三类问题全部由引擎拦截（单元测试逐场景覆盖）。
> 6. **当日复盘布局修复**（用户反馈）：报告 grid 布局目录/内容列序错位（aside 在前占
>    主列）→ grid-template-areas 修正；手机端长文本溢出（result-head/degraded-list）
>    + 目录胶囊 flex:none 不收缩 → overflow-wrap + flex:1 修复；三档视口
>    （1280/390/360）scrollW=视口宽、无 JS 错误。
> 7. 验证：全量 pytest **534 passed**（52 验收用例 skip 待启用）；一键回测 **16/16 全绿**。
> 8. 遗留：LLM 抽样 3 天在回测环境不可用（opencode 端点代理）降级规则引擎；
>    test_r12_acceptance.py 52 项验收用例待后续启用对齐；节假日表未内置（注明）。

> 2026-08-08 收尾快照（本次会话暂停点，下次续接）：
> **已完成并验证**：R12 账户系统全链路（引擎 13 手算测试/534 passed/回测 fixture
> 29 笔成交 0 违规/一键回测 16 项全绿）；当日复盘布局修复（grid-template-areas +
> 长文本换行 + 目录胶囊收缩，三档视口无溢出）；模拟账户空数据修复（api.js 解析
> data.accounts 对象结构）。
> **已改代码未验证（下次首先验证）**：
> 1. 侧栏历史复盘 404 修复：App.jsx handleOpenReview 与任务完成补全改用
>    api.getReviewByDate（旧 /{date}/{time} 路由已移除导致 Not Found；App.jsx:350
>    注释已自述该坑但此前未修）；需 build + 浏览器验证 + **补 regression 断言**
>    （用户质疑「为何测试没测出」——冒烟未覆盖侧栏历史点击路径，必须补）；
> 2. 手机端复盘目录显示优化：styles.css 1099px 规则改 flex-direction:column
>    （标题一行 + 目录列表整行横滚 334px），未构建验证。
> **遗留**：LLM 抽样降级；test_r12_acceptance.py 52 项待启用；节假日表；
> 分析师名称疑问（代码层未变，待用户补充现象）；vite dev 5174/代理 8501 已修。
> **下一步计划**：① build + Playwright 验证侧栏历史点击（含手机 390 目录观感）；
> ② regression.py 浏览器段补「侧栏复盘记录点击 → 报告展示无 Not Found」断言；
> ③ 全量 pytest + 一键回测回归；④ 启用 R12 验收用例对齐；⑤ PROGRESS 最终登记。

> 2026-08-09 续接完成（收尾快照的待验证项全部闭环）：
> 1. **侧栏历史复盘 404 修复验证**：App.jsx handleOpenReview 与任务完成补全改用
>    getReviewByDate（旧 /{date}/{time} 已移除）；桌面浏览器点击侧栏复盘记录 →
>    报告正常显示、无 Not Found；**regression.py 新增「侧栏历史复盘可打开」断言**
>    （补此前冒烟盲区），一键回测 17/17 全绿。
> 2. **手机端复盘目录显示修复**：1099px 规则 flex-direction:column（标题一行 +
>    目录列表整行横滚）；list 的 flex:1 1 0 在 column 主轴下把高度压塌（h=4）
>    → 改 flex:0 0 auto；chips 恢复 26px 高度、scrollW=视口宽。
> 3. **交割单指标全 0 修复**（用户反馈「明细出来了但数据全为 0」）：
>    根因 metrics.py `_collect` 用 getattr 取值——后端传 to_dict() 的 dict
>    （getattr 对 dict 返回 None → 全部记录被过滤 → 指标全 0）；
>    新增 `_field` helper 兼容 dict/对象；另清理旧 metrics.json 缓存
>    （修复后旧缓存仍返回 0，需删缓存重算）。验证：2 闭环/胜率 100%/
>    TWR 1.03%/盈亏 +12148.50 前端正常显示。
> 4. 回归：全量 pytest **534 passed**；一键回测 **17/17 全绿**。
> 5. 遗留：test_r12_acceptance.py 52 项验收用例仍 skip（待后续启用）；
>    用户交割单（72d0b7691cb2，31 条含转账/期初卖出）无完整闭环属数据口径
>    （指标显示 —/0 为正确语义，非 bug）；节假日表未内置。

> 2026-08-09 二轮（用户反馈三项，仅交割单与聊天，复盘功能零改动）：
> 1. **交割单分析去掉讨论与分歧环节**：trades/analyst.py 的 analyze LLM 路径与规则引擎
>    路径均删除主持人/辩论循环（debate_history 恒 []、round_count 0）；报告章节重排为
>    五节（账户概况/各分析师核心观点/操作意见/幽默标签/风险提示）；build_report_prompt
>    与 rule_report 同步去「分歧与讨论」章节；验证：新生成报告无分歧章节、95 项
>    trades/backtest 测试全过；复盘（graph.py）未动。
> 2. **历史交割单列表**：后端 GET /api/trades（按时间倒序：trade_id/时间/记录数/
>    has_analysis/摘要；单目录损坏跳过）；GET /api/trades/{id} 回读 analysis.json
>    （历史报告可回看不重新生成）；前端 TradesTab 新增「历史交割单」卡（列表+点击回看
>    明细/指标/报告）；api.js getTrades 修 mock 优先（R12 同款坑）为请求优先；
>    样式按 UI 知识库规范（列表卡片式/触控/空态）。
> 3. **聊天气泡 user 消息左侧 bug**：user_msg 事件追加原始 payload（无 isUser）→ 渲染走
>    左侧分支；回答完成整体归一化后才回右侧。修复：user_msg 分支 normalizeChatMessage
>    后再 setSession。
> 4. 验证：全量 pytest **534 passed**；一键回测 **17/17 全绿**（含聊天链路与侧栏历史断言）；
>    交割单历史列表 5 项、点击回看 31 笔单+报告正常。

> 2026-08-09 三轮（用户反馈：未实现盈亏全空 / 复用已有爬虫能力 / 盈亏百分比格式）：
> 1. **估值链复用 realtime**（metrics.py `_fetch_latest_prices`）：akshare 全市场表失败后
>    逐只复用 `realtime.fetch_stock_quote`（东财→腾讯→通达信协议），再兜底单股东财直连；
>    修复三个隐蔽 bug：① akshare 表抛异常短路后续链（包 try）；② 单只失败清空已成功价格
>    （改逐只 continue）；③ `_worker` 缩进错位致闭包 NameError（整体回缩 4 空格）。
>    效果：东财 502 时腾讯/通达信仍可取价，持仓不再全部按成本估值；实测 31 笔单
>    未实现盈亏 **+83.95**（紫光 −5.0），`market_value_source=akshare`。
> 2. **盈亏百分比格式**（TradesTab 股票汇总表）：已实现/未实现盈亏列按买入金额
>    （buy_amount）显示「金额（百分比）」，如 `+9,747（+5.42%）`；无基数（期初卖出）只显金额。
> 3. 测试：test_price_fetch_fallback_chain 增加 realtime mock（验证 realtime 失败→单股东财
>    兜底链路）；全量 pytest **534 passed**；一键回测 **17/17 全绿**。
> 4. 遗留：旧 metrics.json 缓存（cost 结果）需清理后重算（已记录操作方式）；
>    按股票统计/过滤转账（R13）已在新线程实现；后续估值可进一步接通达信协议优先。

> 2026-08-09 R13 交割单「解析明细」展示优化（新线程开发，multi-agent-dev）：
> 1. 需求（docs/requirements-r13.md）：解析明细去掉银行转账等非交易操作、以股票为单位统计、
>    覆盖单向操作临界（只有买入/只有卖出/部分平仓）；
> 2. **数据基础确认**：metrics.stocks 已按股票聚合（buy/sell 笔数量额、已实现/未实现盈亏、
>    status closed|held），实测覆盖 买0卖N（期初卖出 301526）、买N卖0（持仓中 600378/300058）、
>    部分平仓（000938）——后端零改动，仅前端展示层；
> 3. **前端 TradesTab 改造**：明细区改为「股票汇总」表（代码/名称/状态/买入/卖出/
>    已实现盈亏/未实现盈亏，红涨绿跌）；状态口径：买0卖N→期初持仓卖出、买N卖0→持仓中（未卖出）、
>    held→持仓中、closed→已平仓；每行展开该股逐笔（仅 BUY/SELL，转账不显示）；
>    未实现盈亏为 0 显示「—」（成本估值无意义）；新增 fmtMoney/fmtSignedMoney；
> 4. 验证：浏览器实测 9 只股票、临界标注正确、转账零残留、展开逐笔 14 行无 JS 错误；
>    全量 pytest **534 passed**；一键回测 **17/17 全绿**。

> 2026-08-07 二轮：bat 编码修复 + 东财 502 延迟行情兜底：
> 1. **bat 乱码修复**：改端口时误按 GBK 读写导致 启动服务.bat 损坏（先被清空再恢复）。
>    处理：git checkout 恢复 HEAD 版（UTF-8 无 BOM + CRLF）→ 端口替换 8501 → 启动行改回
>    守护脚本调用（HEAD 版为直启，无崩溃自动重启）。教训：**bat 为 UTF-8 无 BOM + chcp 65001**，
>    不得按 GBK 处理；git show 对中文文件名在子进程管道中会取空（size=0），恢复一律用
>    `git checkout -- <文件>`。
> 2. **东财 502 诊断**（用户反馈「之前不会这样」）：实测真实网络——东财官网/quote/push2delay
>    均 200，但 push2/push2his 行情推送域（含全部数字子域）统一 502，HTTPS 被掐断：
>    东财行情节点对当前出口 IP 限流/故障（非本项目问题，回归测试高频请求或为诱因之一）。
> 3. **修复（延迟行情兜底）**：push2delay.eastmoney.com 实测可用且字段格式一致——
>    market_extra._em_board_quote 失败后兜底 push2delay（BK1158/BK1641 及风格坐标价值/成长）；
>    realtime.fetch_indices 失败后兜底 push2delay；source 如实标注「东财板块·延迟行情」/
>    「东财延迟行情」+ degraded 标注，绝不静默。880003（平均股价）不依赖东财行情域。
> 4. **验证**：真实 502 期间 API 返回 微盘股 3408.05(+0.26%)/高股息 983.46(-0.65%)，
>    src=东财板块·延迟行情；全量 pytest **377 passed**；一键回测 **15/15 全绿**；
>    8501 已由守护脚本加载新代码重启。

> 2026-08-10 板块历史数据修复（同花顺 v4/v6 line 直连，调研 akshare 源码后实施）：
> 1. **根因**：东财 push2his 历史数据集群故障（真实浏览器 ERR_EMPTY_RESPONSE，非 FlClash/
>    UA/指纹问题；push2 实时、push2delay、quote、datacenter-web 均正常）；akshare 历史板块
>    接口依赖 push2his 或 py_mini_racer（Windows 死锁被禁），双双不可用。
> 2. **方案（社区主流，akshare stock_board_industry_index_ths 同款数据源）**：
>    d.10jqka.com.cn v4 按年日K（2026.js 含全年）+ v6 last（最近约 140 根）兜底，无需
>    py_mini_racer/v cookie；板块列表 q.10jqka.com.cn/thshy/ 直连解析（90 行业）。
>    实现：stock_data.py 新增 _ths_sector_names_line/_ths_sector_hist_line，_sectors_from_hist
>    同花顺分支改用直连，遍历改 ThreadPoolExecutor 并发 8（14.9s→2.1s）。
> 3. **限流坑**：q.10jqka.com.cn 列表页连续请求即被拒（第 2 次解析为空）→ 板块列表加
>    进程内 TTL 缓存（1h）；d.10jqka.com.cn 日K 不限流（实测连续可过）。
> 4. **验证**：7 月 23 交易日批跑全部成功（23/23，0 降级，平均 2.2s/天，结果
>    work/sectors_july_result.json）；数据与东财旧缓存一致（7-15 白酒 +5.13% 等）；
>    新增 tests/test_sectors_ths_line.py 6 用例全过；数据层回归 34 passed；
>    全量 537 passed（4 个既有失败：3 个 LLM Key 未配置 + 1 个通达信本地日期过新，与本次无关）。
> 5. **遗留**：历史资金流仍无在线通道（push2his fflow/daykline 挂、同花顺无资金流 K 线、
>    东财报表名不存在）；当日资金流可用 push2 ulist（f62 主力/超大/大/中/小单，已实测）。

> 2026-08-10 二轮：7 月复盘完整跑通 + 复盘速度优化（5-7 分钟/天 → 并发 2 天 ~5.7 分钟墙钟）：
> 1. **7-01~7-03 完整复盘跑通**（LLM 5 分析师正常，报告落盘 data/reviews/）；修复后
>    科创50 全字段有值、创业板最低/涨跌幅恢复、天气正常（晴/偏暖 66 分）、板块走新直连源；
>    剩余真实缺失：上证/创业板成交量+成交额（akshare 腾讯日K字段波动）、7-02 财新/央视
>    无存档（历史资讯预期缺失）。
> 2. **慢因定位（量化）**：取数已并行（fetch_close ~24s，最慢指数 16s）；天气已复用 blocks
>    （省 20s）；瓶颈是 LLM 链路——opencode.ai 长上下文调用 15-70s/次（网关波动，直连与
>    代理 7890 均验证），串行节点链（news→trend→sentiment→host→debate→report）×
>    分析师工具轮（3 轮）→ 单天 5-7 分钟。
> 3. **四项优化**：① graph._callbacks 默认关闭 langfuse 回调（实测同步 OTLP 导出把单次
>    LLM 调用从 15s 拖到 180s+，是最大头；LANGFUSE_CALLBACKS=1 可开；复盘级 trace 仍由
>    quality.trace_review 独立上报）；② 进度钩子改 ContextVar（graph.set_progress_callback/
>    _emit），分析师线程池每任务独立 copy_context，后端去掉 _ENGINE_LOCK → **job 可并发**；
>    ③ 辩论 max_rounds 3→2、分析师工具轮 3→2；④ 天气复用 fetch_close blocks
>    （compute_market_weather 优先，兜底 fetch_market_weather）。
> 4. **验证**：并发 2 job（7-01/7-02）墙钟 340s（串行需 ~580s，省 40%），进度各自独立；
>    单天 4-6 分钟（LLM 网关波动下限）。
> 5. **遗留**：上证/创业板指数 volume/amount 字段缺失（akshare 腾讯日K源不稳定，备用源
>    腾讯日K amount=None）；test_graph.py 17 个 LLM 语义失败未更新（既有）。

> 2026-08-10 三轮：8 月模拟账户系统完整验证（重置账户 → 交错批跑 → 规则校验）：
> 1. **流程修正**：并发跑 5 天复盘（无结算）会让分析师看不到前一天成交（get_positions 空）
>    →「持仓处理：无持仓」幻觉 + 重复推荐买入。正确流程 = **复盘与结算严格交错**
>    （run_aug.py 提权跑，复盘 job 走服务端、结算写 H:\data\accounts 需提权）。
> 2. **工具轮上限回退**：曾改 2 导致 LLM 分析被截断（get_positions→search→calc_qty→
>    place_order 需多轮）→ 5/5 分析师空回复零推荐；恢复 3 后正常（25 人次仅 3 个偶发空回复）。
> 3. **验证结果（8-03~8-07 交错批跑，31.4 分钟）**：5 账户全部开户/交易/结算正常；
>    总资产（成本口径）99959~99979（≤10w，仅扣手续费 5 元/笔）；目标价撮合正确
>    （8-05 000636 成交 @52.5~54.5）；边界计算生效（8-06 alang 资金不足拒绝第 4 笔）；
>    T+1 冻结正确（当日买入 frozen>0）；持仓处理结构化输出（「持仓处理：持有 000636」，
>    8-07 退潮期 4/5 分析师明确持有、无卖出推荐——语义正确）；pending 全部 settled。
> 4. **遗留**：current_date 仅随结算日推进（无 pending 的日子不推进，冻结显示待解冻，
>    不影响成交正确性）；000636 被多数分析师同时推荐（提示词同质化，可后续加差异化约束）；
>    8-06 偶发 LLM 150s 超时（重跑恢复，网关波动）。

> 2026-08-11 R14 两段式跑批完成（multi-agent-dev：A1 计划层 / A2 行情缓存+重放 /
> A3 批跑脚本+并轨 / A4 测试先行 84 用例）：
> 1. **plan.json 唯一真源**：`data/reviews/{date}/plan.json`（graph place_order 工具与
>    正则提取统一写计划 registry → job 收尾 finalize 整文件覆盖落盘；同日重跑无残留旧计划；
>    不再写 pending.json）；
> 2. **账户全量重放**：accounts/replay.py 只消费 plan.json + quotes_cache（确定性、不调 LLM），
>    AssertionChecker 合规断言移植（超资金/T+1/涨跌停/非整百/裸卖空 → violations + 退出码 1）；
> 3. **行情缓存**：quotes.py `data/quotes_cache/{date}.json`，重放离线全命中不联网，缺失如实标注；
> 4. **批跑脚本**：work/review_batch.py（run 逐日复盘+产物校验+LLM 降级重试≤3；validate 输出
>    可用/需重跑清单）+ work/account_replay.py（--rebuild 全量重放）；旧脚本（settle_pending/
>    settle_all/backfill_pending/backfill_snapshots/run_aug/july_backfill/replay_from_reviews/
>    check_accounts）归档 work/archive/；scripts/backtest_accounts.py 并轨删除；
> 5. **历史复盘决策（用户确认）**：不迁移——validate 校验后直接重跑（7-01~8-06 全部缺
>    plan.json 判需重跑）；
> 6. **验证**：全量 pytest **620 passed / 52 skipped**；一键回测 **17/17 全绿**；
>    修复三处：① quotes.py os.environ.pop 删代理变量不恢复 → 污染后续测试（chat 取数被
>    迫直连限流；Session(trust_env=False) 已足够，删除 pop）；② tdx 本地数据更新到 08-10，
>    断言改动态（覆盖近期且 ≤ 今天）；③ sectors/breadth 降级测试补 mock
>    （_ths_sector_names_line / realtime.fetch_breadth_realtime，PLAYBOOK 教训）。
> 7. **遗留**：8 月历史复盘补跑待执行（LLM 依赖，单天 5-7 分钟）；8-07~8-11 复盘待生成。

> 2026-08-12 R15 M2+M4 交付（分析师节点合并方案 B + 空观点修复 + 工具结果脱敏）：
> 1. **R2 节点合并（方案 B）**：trend_analysts + sentiment_analysts 合并为单一 analysts 节点，
>    一次并行跑全部 5 位分析师（趋势派 2 + 情绪派 3），情绪派不再注入 trend_context；
>    进度事件保留 trend（n/2，pct 20+）→ sentiment（n/3，pct 30+）两阶段语义，
>    全部完成后按组排序统一发射（先 trend 后 sentiment）——backend _progress_cb 按消息
>    n/m 累计 analysts_done（sentiment +2）不会倒挂，job 契约（analysts_done/total=5）不变；
>    图结构 fetch → validate → news → analysts → host ⇄ debate → report。
> 2. **R4 空观点修复**：_llm_analyst 兜底区新增空话检测（_is_empty_opinion：<30 字符或命中
>    模式词表「查询返回为空/无明确观点/工具查询完成，无/无法获取/本轮工具/无观点输出」，
>    检测失败默认按空话处理）→ 有工具调用走工具摘要兜底、无工具返回 None 走规则引擎；
>    工具空结果显式化为 {"error": "无数据（代码X）"}（search_history 空 results /
>    get_stock_info 数据缺失），杜绝「本轮工具查询返回为空，无明确观点输出」类空话进报告。
> 3. **R1 剩余落点（工具结果脱敏）**：每个工具结果追加给 LLM 前经
>    sanitize_for_llm 清洗（dict/JSON 文本均覆盖，失败原样），落盘 tool_log 保留原样；
>    ANALYST_BASE_TEMPLATE 增加两条铁律（工具无结果时基于已有数据输出观点、只写代码禁写名称）。
> 4. **测试**：新增 tests/test_analysts_merge.py 13 用例（图结构/进度契约/方案B不注入/
>    空话兜底/脱敏/空结果显式化/正常内容不受影响）；test_r15_acceptance R2/R4 去 skip 全绿；
>    验收命令（test_analysts_merge + test_graph + test_backend）全绿；全量 pytest
>    **680 passed / 52 skipped / 0 failed**（3 轮，中途 test_index_amount 3 个失败为
>    R3 并行线契约迭代、watchlist 1 个为全量顺序污染，均单独复跑绿后全量收敛全绿）。
> 5. **遗留**：R3 指数成交额回填（并行线）与 R0 资讯屏蔽前端收尾按 R15 验收标准另行交付。

> 2026-08-13 架构优化 + 模拟账户重点修复（全量 **715 passed / 52 skipped / 0 failed**，
> 前端 build 通过；测试基线 = 系统 python 3.13，见 PLAYBOOK 铁律）：
> 1. **模拟账户现价 bug（用户点名）**：持仓「现价/市值/浮盈」原先用 fetch_stock_quote 实时价，
>    现改为从「最近结算日快照」的 positions 明细读历史收盘价；engine.snapshot 新增 positions
>    字段（close/market_value/qty/estimated），unrealized_pnl 与 total_assets 同源一致。
>    旧账户（bingchuan 等）快照无 positions → 回退成本价 + degraded 标注「历史价缺失」。
> 2. **重放/撮合逻辑**：settle_plans 无计划结算日也推进 current_date + 解冻 T+1 + 落快照；
>    quotes.py 历史日期不再采信腾讯实时价（宁标缺失不错撮）。
> 3. **历史持仓**：engine.closed_trades 聚合已平仓股票，侧栏模拟账户页新增「历史持仓」卡片；
>    报告页内嵌账户区块按方案 B 整体移除（账户统一走侧栏）。
> 4. **架构优化**：抽取 llm_runtime.py 统一 LLM 运行时（graph/trades 两引擎复用，顺带修复
>    交割单分析 llm/llm_strict 未导入导致 LLM 路径恒失效的既有 bug）；host/debate 走硬超时；
>    删 _ENGINE_LOCK、validate_data 死路径、旧 CLI src/main.py+app.py、report 调试转储收敛、
>    chat lru_cache 可重置；backend/main.py 拆路由（jobs/snapshot/routes_* 5 子模块）+ CORS
>    环境变量化；跌停 NaN bug（同花顺跌停 0 只省略 num 字段 → build_zt_block 缺省 0）。
> 5. **stock_data 拆分失败与恢复**：子代理按行号切片拆分 3100 行 stock_data.py 失败，丢失
>    10 个函数 + 6 变量（未提交改动）。已从 __pycache__ 的 3.13 字节码反汇编完整恢复
>    （fetch_stock_detail/_backfill_index_amount/_ths_sector_hist_line/_parallel_blocks 等
>    + 全部接线），数据层 125 passed 确认无功能丢失；stock_data.py 恢复单文件，放弃拆分。
>    PLAYBOOK 已追加踩坑条目（flash 不适合大文件机械拆分 + .pyc 恢复方法）。
> 6. **遗留**：① 重放重建历史快照（`python work/account_replay.py --rebuild`）待执行，执行后
>    旧账户现价才能显示真实历史收盘价；② 根目录约 40 个 pytest-cache-files-* 沙箱缓存残留
>    （沙箱删不掉，建议主环境清理）；③ pyproject.toml 已移除 streamlit 依赖（app.py 已删）。

> 2026-08-13 深夜 R16 复盘体验优化（用户 6 项反馈 + 建议1情绪周期 + 建议4结算标注，3 子代理并行）：
> 1. **去辩论节点**：graph.py 删 debate_node/_RULE_DEBATE/router，图改
>    fetch→validate→news→analysts→host→report；host_node 注入 `host_analysis`（LLM 原文或规则降级），
>    build_result 恒 debate_history=[] / round_count=0；报告「分歧与讨论」→「分歧点」（prompts MODE_INFO
>    6 模式 + build_report_prompt + quality.py REVIEW_SECTIONS + test_quality_r10 样例已同步）。
> 2. **成交额亿元**：build_report_prompt 单位铁律——成交额列 ÷1e8 转亿元（2 位小数），成交量原样。
> 3. **买入带原因**：ANALYST_BASE_TEMPLATE/build_account_block/报告推荐格式三处冻结格式加 `，原因是XXX`；
>    plan_extractor 正则本就兼容（finditer 非全匹配），新增 3 条带原因用例锁定。
> 4. **深证成指加回**：stock_data.py INDEX_CODES 恢复 shenzhen=sz399001（sz 前缀通用判定，与创业板同路径），
>    报告指数表改四指数、前端 DataTab 指数卡过滤加回。
> 5. **去涨跌分布**：snapshot.py `_snap_breadth` 移除 9 档 distribution（保留 up/down/limit_up/limit_down/
>    涨跌比）；前端删 BreadthChart（Charts.jsx 导出 + ReportView 图块）。
> 6. **情绪周期定位**：market_extra.py 新增 `classify_emotion_cycle(blocks)->str`（四阶段：冰点≤20/退潮/主升/
>    修复发酵，规则引擎纯计算）；graph.fetch_data_node 注入 state.emotion_cycle；报告市场概况加
>    「情绪周期定位」行。实测 8-13 数据→「退潮（红盘率45.8%、炸板率39.2%）」。
> 7. **前端其他**：ReportView 左侧菜单只显示「{date} 复盘」；stages.js/App/api/mock/normalize 删辩论；
>    账户「结算至」标注（前端 .settle-note + normalize.current_date + 后端 _summary 补 current_date）。
> 验证：三路子代理测试各绿（引擎 110 / 数据 83+55 / 前端自检）；后端已重启加载新代码。
> ⚠️ **遗留（明日继续）**：全量 pytest 现 **2 failed**（tests/test_data_modes.py::test_mode_blocks_all_success
>   [intraday_am] 与 [intraday_pm]，`degraded_flag is False` 断言失败），**隔离复跑必现（非 flaky）**；
>   regression.py 因此 16/17（仅「全量 pytest」一项 FAIL，build/API/聊天/浏览器冒烟全绿）。
>   排查方向：intraday_am/pm 与 noon 的唯一差异在 minute 块 end_time 用 `_mode_end_time`（依赖 _now() 当前
>   时间，测试用固定 TODAY 日期，疑似时间相关降级），需先定位是 minute 还是 index_trend 块 degraded（在
>   测试内打印各 block.degraded 即可）；与深证成指改动是否相关待确认。修复后重跑 regression.py 至 17/17。
> ⏳ **下一批（用户已确认口径，待启动）**：首页 4 点——① 去掉「今日数据」个股行情查询卡（个股详情保留）；
>   ② 去掉纳斯达克卡片；③ 市场天气增强敏感度（九档，每100家跌5%以上扣5分/跌7%以上扣10分，允许负数、
>   下限-100，需数据层 finer 分档或「跌停」作 7%+ 代理）；④ 观察列表加 margin-top（与风格坐标粘连）。

2026-08-16 回测断点续跑（resume）+ 产物校验失败保留行情快照（requirements-backtest-resume）：
1. **backend/backtest.py**：_run_loop(bt, start_idx=0) 循环从 start_idx 起步（续跑起点）；
   新增 
esume_backtest(bt_id)（单 running 互斥 409 / done 409 / 找首个非 done 日 / 重置该日起为
   pending / status=running + finished_at=None + days_done 重算 / **不重置账户** / daemon 线程续跑；
   内存 BACKTESTS 优先，否则从 manifest+progress.json 磁盘重建）；_run_one_day 产物校验失败
   delete_review 改 keep_snapshot=True（防复发，好快照保留）。
2. **backend/routes_backtest.py**：POST /api/backtest/{id}/resume → resume_backtest。
3. **reviews.py**：delete_review(date, time=None, *, keep_snapshot=False)——True 时删整目录后
   恢复 market_data.json（保留行情快照），默认 False 删整份（回归既有）。
4. **前端**：api.js 
esumeBacktest(id)；BacktestView.jsx HistoryList error/cancelled 项加
   「续跑」小按钮（onResume，独立按钮不破坏整行 onClick），handleResume → resumeBacktest →
   getBacktest 快照 → beginPolling。
5. **验证**：test_backtest + test_backtest_live_diag + test_storage_reviews == **70 passed / 0 failed**
   （含 H1/H2/H3/H4/H6 共 6 个 H 用例全绿）；手动 .tmp 脚本构造 error 回测 → resume_from=失败日 →
   续跑全 done；delete_review keep_snapshot 快照仍在；前端 
pm run build 通过（bundle 含 /resume、
   续跑/Ui）。H5 前端按钮 + build 通过。真实续跑 bb45（从 5-08）需用户确认后另跑。


2026-08-16 分析师选股与交易模型打磨（基于 2026-04-01~08-14 最新回测）：
1. **提示词**：`ANALYST_BASE_TEMPLATE` 新增「你的专属交易模型」区块 + 买入价纪律
   （买入价必须严格低于涨停价，卖出价必须高于跌停价，修复回测中大量「目标价=涨停价被拒买」）；
   `build_analyst_prompt` 增加 `trading_model` 入参；`build_account_block` 按 `skill_id` 注入专属兜底参数。
2. **每位分析师交易模型**：`skills/*/skill.json` 新增 `trading_model` + `trading_params`，
   并将 prompt 内系统兜底同步为：alang 趋势 -5%/+10%/13天；baxiaoxian -4%/+6%/3天；
   bingchuan -5%/+8%/5天+回落4%；tiechui -4%/+6%/3天；yangjia -5%/+8%/3天；
   同时收紧仓位/只数（单票≤2-3成、总仓≤5-7成、最多2-3只）。
3. **撮合兜底**：`engine.ANALYST_TRADING_DEFAULTS` + `apply_analyst_conditional_defaults`，
   `replay.py` 改为按 `skill_id` 取专属默认，显式字段仍优先。
4. **UI 主题**：styles.css 主强调色从紫改为 A 股复盘红（#c8423c），品牌标/侧栏按钮/回测图同步；
   index.html theme-color 改红。
5. **文档**：`docs/requirements-trading-v1.md` 新增 §七 分析师专属交易模型表。
6. **验证**：待跑全量 pytest / regression.py（交付前补跑）。


2026-08-16 已保存优化前回测基线：`backups/backtest_baseline_20260816/`
- manifest.json：当前回测 ID + 各分析师期末账户快照；
- compare_baseline.py：下次重跑后自动对比收益率/总资产/已实现盈亏/成交拒绝/最大回撤；
  已支持区间对比：`--start 2026-08-01 --end 2026-08-14`（默认旧基线目录
  `backups/snapshot-20260815-103351/data/accounts`）。


2026-08-16 模拟下单卖出方向 BUG 修复（用户实测：爱在冰川/国电南瑞卖出 target=24.97 > 当日 high=24.90 被误拒）：
- 修复：`engine.place_order` 卖出 target>high 不再拒绝，按“开盘即卖出”处理，成交价=开盘价（开盘价缺失用最低价兜底）；
- 同步：`docs/requirements-order-fill-direction.md` G4 改为成交；`tests/test_accounts_engine.py::test_g4_sell_target_above_high_fills_at_open`；
  `prompts.py` A 股规则铁律文案更新。
- 排查：全项目仅 `engine.place_order` 存在该对称误拒；条件单 gap 逻辑已按开盘价处理，无需重复修复。


2026-08-17 回测表现分析 + 分析师提示词回测纪律 + 回测账户摘要持久化：
- 基于最近回测与交易明细（含 4-08 全量基线、5 月逐笔、work/replay_result.json 涨停价拒买）：
  - 阿狼：全量基线最佳（+4.86%），但 5 月单日同时铺 3 只“打野”且全部次日止损，回测改进=单日新建≤2只/同板块≤1只/连续未成交放弃/止损后降仓；
  - 拔小弦：5 月 -2.34% 相对稳健，但大量低吸挂单远低于次日低点导致拒买，改进=贴近 5/10 日线真实支撑、止损后当日不开新仓；
  - 爱在冰川：5 月 +0.09% 唯一正收益，但大赚后仍买低价 2 板并大额止损，改进=大赚后次日降仓/退潮尾段不买低价 2 板；
  - 铁锤：5 月 -6.41%，多次“未触及目标价”+止损后重复卖同股，改进=确认不足空仓、买入价贴近关键承接、止损后等新确认；
  - 炒股养家：5 月 -4.65%，有移动止盈盈利但被后续重仓亏损吞掉，改进=止盈后不立即同等仓位追新、退潮只用试错仓。
- `prompts.py` ANALYST_BASE_TEMPLATE 新增通用「回测纪律」；5 份 `skills/*/skill.json` trading_model 追加各自回测改进。
- `backend/backtest.py` 回测完成时保存 `accounts` 摘要（期末资产/收益率/已实现盈亏/成交拒绝/最大回撤/胜率/盈亏比/平均持仓），`progress.json` 持久化；前端 BacktestView 新增「回测期末账户摘要」表。
- 文档：requirements-trading-v1 七表与 skill.json 对齐 + 新增回测纪律说明；requirements-backtest-ui 数据模型补 accounts 字段；测试补 accounts 字段断言。


2026-08-17 根目录 npm 代理修复：
- 用户反馈 `npm error ENOENT ... H:\stock_review_crew\package.json`：此前前端 package.json 在 `frontend/`，根目录直接 `npm run build` 会找不到 package.json。
- 新增根目录 `package.json`，将 `dev/build/preview/verify:normalize` 代理到 `frontend/`，根目录 `npm run build` 可正常执行。
- README 同步更新：开发/构建命令可直接在根目录执行。


2026-08-17 八项体验优化（用户反馈）：
1. 市场天气加入涨跌分布区间评分：Tushare 全市场分布新增 0-3/3-5/5-7/7-10/>10 涨跌对称细分区；compute_market_weather 按区间数量加权评分，保留旧键兼容。
2. 风格坐标灵敏度放大：_dim_score 由 1pp=2 分提升为 1pp=10 分，小差异更可辨。
3. 今日数据额外指数新增「同花顺全A（880011）」，放在平均股价后，一行 4 个；CSS 改 repeat(4,1fr)。
4. 复盘降级标注收敛：_dict_to_md 跳过 degraded/degraded_reason 元数据；_block_md 仅在全链路数据缺失时展示降级说明，主源失败备用源成功不再标为降级。
5. 复盘报告目录（左侧 TOC/返回顶部）移除，报告卡片全宽展示。
6. 模拟账户列表改为表格展示，新增「平均仓位」字段（快照 position_mv/total_assets 均值）。
7. 收益率曲线改用账户实际 initial_cash 计算，避免修改初始资金后曲线起点错误；账户概览由卡片改为单行文本展示并优化颜色。
8. 分析师提示词从设置页拆分为独立页面 SkillPromptsView，使用 Markdown 预览 + 可编辑保存；侧栏新增「分析师提示词」入口，设置页保留跳转按钮。
验证：全量 pytest 828 passed / 52 skipped；npm run build 通过；后端已重启加载新代码；公网隧道仍可用。

2026-08-17 监管异动·单股计算（首版）：
- 新增 src/stock_review_crew/tools/anomaly.py：3/10/30 日偏离 + 同向计数 + 未来模拟；口径=主板3日±20%/创业板科创±30%/10日+100-50%/30日+200-70%/10日同向≥4。
- 新增 backend/routes_anomaly.py：GET /api/anomaly/stock?code=&days=（6位代码/1-10天）。
- 前端 AnomalyView.jsx（监管异动·单股计算：搜索/指标卡/预警/未来模拟/30日明细）+ 侧栏入口 + App 路由。
- 新增 tests/test_anomaly.py（离线 3 用例）。全量 pytest 830 passed（1 个既有 quotes_cache 网络用例失败，与本功能无关）；npm run build 通过；后端已重启，实盘接口 600664 返回正常。

2026-08-19 监管异动·对齐参考图（盘中快照口径）：
- 定位：参考图（yidong.jpg @11:30）是盘中 11:23 截图——600664 现价 9.02、上证A股 4101.83，故 3日1.87/10日44.70/30日197.85；而收盘日K已是 9.06/4083.28，导致数值漂移。
- 修复：新增 `_realtime_quote`/`_apply_realtime_snapshot`，查询时用腾讯实时现价替换当日日K收盘价（仅当日、仅当有盘中/收盘实时价；收盘后实时价=收盘价无副作用），使盘中查询与参考图 1:1 对齐。
- 修复腾讯日K在后端失效：`_records_from_tx_kline`/`_minute_bars_from_tx` 加 `proxies={"http":None,"https":None}`，避开未启动的本机代理，后端现稳定走“腾讯日K”（/api/anomaly sources=腾讯日K）。
- 新增历史口径：`/api/anomaly/stock?code=&days=&date=YYYY-MM-DD` 支持 as-of 截断（不叠实时快照），便于核对历史上某日的官方偏离值。
- 补充 tests/test_anomaly.py：新增 as-of date 用例（4 passed，离线）；后端已重启验证。

2026-08-19 监管异动·对齐“开盘啦”异动提示（kaipanla_yidong.jpg）：
- 参考图给出 6 只股的目标窗口与“触发异动涨幅”（当日涨跌幅临界值）：600664(30日200%→-0.53%)、600272(10日100%→2.65%)、300534(30日200%→10.47%)、603221(复牌10日同向→7.06%)、301282(10日100%→16.13%)、688296(10日100%→11.40%)。
- 口径确认：触发异动涨幅 = 使“区间最大累计偏离(复利)”达到阈值所需的当日涨跌幅（600664 与参考逐点一致 -0.53）。
- 板块基准修正：科创板 688/689 由“上证A股指数”改为“科创50指数(sh000688)”；创业板 300/301 由“深证A股指数”改为“创业板综指(sz399102)”。对齐后：600664 -0.53/ref-0.53、600272 2.67/2.65、301282 16.05/16.13、688296 11.2/11.4 高度吻合；300534 9.83/10.47 略偏（疑为数据源收盘价差异）。
- 603221 属“复牌10日同向”特殊规则：引擎已按 10 日同向≥4 标注 severe（当前同向 5 次），参考图 7.06% 为复牌窗口内模拟触发涨幅，非通用偏离。
- 说明：证监会/交易所历史规则检索因本环境 web_search 余额不足无法在线拉取，以上阈值沿用既有定稿口径（主板3日±20/创业板科创±30/10日+100-50/30日+200-70/10日同向≥4）。
- 后端已重启：/api/anomaly 现对 688 用科创50、对 300/301 用创业综指，数据源=腾讯日K。
- 涨跌幅显示精度修复（全面排查）：`anomaly.py:392/394` 的 `stock_pct`/`index_pct` 本应为小数，原 `round(...,2)` 会把 -0.04961 压成 -0.05 再被前端×100 显示成 -5.00%（实际 -4.96%）。已改 `round(...,4)`。排查确认：偏离值/触发价内部全部用 6 位小数（`_pct`@210、`dev`@226、`_rolling_sum`@248、`_max_cum_dev`@260），2 位小数仅用于返回/展示字段（windows/daily/simulation，327-379 行），故**该 bug 不影响任何偏离值或触发价计算**，仅影响日涨跌幅展示。
- 触发价保留精度：`anomaly.py:334/335` 的 `trigger_up_price/trigger_down_price` 由 `round(...,2)` 改为 `round(...,4)`，避免过早 2 位舍入。600664 收盘后触发价=9.0717（显示 9.07）；参考图 9.08 对应 11:23 盘中快照（close 9.02 / max30 197.87 → raw 9.0846 → 9.08），盘中实时口径可复现。
- 沪主板基准指数修正：用户指出今日“上证指数”收盘应为 3894.42，此前误用“上证A股指数(sh000002)=4083.28”。已将 `anomaly.py` 沪主板(60) 基准由 sh000002 改为 **上证指数 sh000001**（000001，收盘 3894.42，当日 -2.40%）。600664 现 idx_name=上证指数，30d max=199.60，触发价=9.0721。
2026-08-21 新分析师「大A仙尊」+ 复盘分析师多选：
- 新增 skills/daxianzun/skill.json：基于《日总结分类_去重_20260821051839.md》108 篇日总结提炼的超短题材轮动低吸模型；止损 -7%、止盈 +10% 后回落 5% 移动止盈、未到 +10% 按个股强度判断、不买 ST/微盘股/次新股/新股；id=daxianzun，group=sentiment。
- src/stock_review_crew/chat.py DEFAULT_ANALYST_IDS 增加 daxianzun；frontend/src/mock.js ANALYSTS 增加大A仙尊。
- 复盘支持选择分析师（可多选）：POST /api/reviews 新增可选 analysts[]；graph.build_graph 新增 analysts 参数，_run_analysts 按 selected 过滤；Sidebar「补做复盘」增加分析师多选；不传 analysts 默认全部交易型分析师。
- 同步更新测试 5→6 位分析师（test_graph/test_analysts_merge/test_backend/test_r11_reviews/test_r15_acceptance/test_backtest_live_diag），相关子集通过；前端 npm run build 通过。
- 按用户要求，所有复盘入口均支持分析师多选：①侧栏「补做复盘」；②ReviewTab 新增「手动生成复盘（可选分析师）」；③BacktestView 新增「选择参与回测的分析师（可多选）」。后端：POST /api/reviews 与 POST /api/backtest/start 均支持 analysts[] 并按 skills 白名单校验；回测 _run_loop 将 analysts 传入 run_review_day。前端 build 通过；后端已重启并验证无效分析师返回 400。
- 分析师多选升级为统一 MultiSelect 下拉组件（frontend/src/components/MultiSelect.jsx）：回测/侧栏补做复盘/ReviewTab 手动复盘均使用该组件，显示已选人数/支持全选/点击外部关闭；前端 build 通过。
- 修复回测分析师未生效：根因是 `frontend/src/api.js` 的 `startBacktest` 重新构造请求体时漏了 `analysts` 字段，导致后端 manifest 中 `analysts: null`、仍跑全部分析师。已补 `analysts: payload && payload.analysts`；重新 build 后生效。
- 修复回测取消无效：`cancel_backtest` 现在置 `_cancel` 后立即将内存状态置为 `cancelled` 并落盘（后台当日仍在收尾但界面即时停止）；`_run_loop` 在每天结束后也检查 `_cancel` 并直接返回取消，不再覆盖成 done。后端已重启。
- 修复分析师阶段“跑很久不出结果/不报错”：根因是 `ThreadPoolExecutor` 的 `with` 语法在 `as_completed(timeout)` 超时后仍会 `shutdown(wait=True)` 等待挂死的 LLM 线程，导致整个回测卡住。已改为显式 `pool.shutdown(wait=False)`，超时后立即用规则引擎兜底继续后续流程；后端已重启。用户触发的卡死回测 bt_20260821_143117_115d 已取消（cancelled）。
- 修复“取消回测报回测不存在”：`cancel_backtest` 在内存找不到时改为从磁盘 `_load_backtest_from_disk` 重建，支持取消重启前遗留的 running 记录；若磁盘也已是终态则返回 409 中文提示。已重启验证，并取消当前卡死的 bt_20260821_144151_31a7。
- 反复卡在“分析师 大A仙尊 开始”：为降低等待时间，`llm_runtime.py` 单次 LLM 硬超时 200s→90s，`graph.py` 分析师批超时 300s→150s；超时后立即规则引擎兜底，避免每次卡 3-5 分钟。已重启。
- 分析“常失败”根因：`validate_run_product` 把「分析师 LLM 降级为规则引擎」也判为产物不合格，导致 lite 回测在 LLM 慢/挂起时反复重试 3 次后整体 error。已改：lite 或 `allow_llm_degrade=true` 时，LLM 降级结果视为成功（仍保留降级标注），避免回测因 LLM 降级整段失败；已重启生效。当前旧回测 bt_20260821_145847_2190 已 error（5/10 天），新回测将受益。
- 修正：回测默认/lite 不再把「LLM 降级为规则引擎」视为成功（避免假回测）；仅显式 allow_llm_degrade=true 才放宽。同时恢复 LLM 单次超时 200s、分析师批超时 300s，减少因超时过短导致的误降级/误失败。已重启。
- 严重修复（撮合行情日期错位）：`fetch_stock_info` 的 Tushare/东财实现原用“截至 date 的最后一行”而非“date 当日行”，导致 08-21 撮合 300142 时拿到旧日 OHLC（low=12.99），错误成交 15.00（真实 08-21 low=15.30/close=16.10）。已改：Tushare/东财必须精确匹配 date 当日，否则报错；`accounts/quotes.py` 增加 `_fetch_tx_history_quote` 用腾讯历史日K精确取 date OHLC，并在主源日期不匹配时回退；已删 8 月 quotes_cache 强制重取。需重跑回测得到正确撮合。

- 业务逻辑对齐：回测账户摘要 `_account_summary` 原先把全部 6 位分析师都列进摘要，即使只选了 1 位。已改为只按 `params.analysts` 过滤输出所选分析师摘要；未指定则全部。另确认 initial_cash 为可持久化设置（data/settings/accounts.json 现为 200000，默认 100000），属可配置项非 bug。





> 2026-08-29 涨停复盘工作台交付（docs/开发计划-涨停复盘辅助.md，D1-D14 已确认）：
> - 新增：tools/kaipanla_api.py（开盘啦 AppAPI 免登录主源）、tools/zt_tracking.py（全量涨停池/过滤/标签/席位/题材/板块聚类/单日流水线）、
>   zt_analysis.py（轮动池/池生命周期 follow/核心股/收盘介入信号/待填CSV）、storage/zt_tracking.py（data/zt_tracking 落盘）、
>   work/zt_tracking.py（手动触发 CLI）、backend/routes_zt.py（/api/data/zt-tracking/*）、frontend ZTReviewTab（新 Tab 涨停复盘，v0.2 设计稿）；
> - 回填最近 10 个交易日（2026-08-14~08-28）；待人工补充 CSV：data/zt_tracking/pending_manual/；
> - pytest 823 passed（9 个环境性失败：真实网络/tdxpy 已装使降级链测试备用源成功，与涨停模块无关）。
