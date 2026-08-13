# Stock Review Crew 全量重构 — 需求规格 v1（2026-08-03 用户逐条确认）

> 本文为唯一契约。草案存档：`docs/requirements-draft.md`；与本文冲突的以本文为准。
> **本文档已吸收 v4（R8 聊天）与 r10-r15 共 7 份增量需求文档（2026-08-13 合并）**：字段与口径
> 真源以本文 §五/§六 为准；增量文档保留供追溯（各文件头部已加「已合并」标注），不再单独作为契约。
> 原则：**不逐项打补丁**，按本文档完成「设计 → 开发 → 测试 → 冒烟 → 验收」整轮交付。
> 目标仓库：[xiaoLin12130/stock_crew.git](https://github.com/xiaoLin12130/stock_crew.git)

## 〇、用户确认记录（Q1-Q10）

| # | 结论 | 落地 |
|---|---|---|
| Q1 | 开盘啦 Cookie：用户将按方法提供 | `.env` 增加 `KAIPANLA_COOKIE`；未提供前竞价数据走降级链并标注；提供方法见 §四.5 |
| Q2 | Tushare token 允许移入 `.env` | `config.py` 删除硬编码 token，仅读环境变量；`.env` 补齐 |
| Q3 | 通达信路径 `F:\tdx`（已确认存在，含 vipdoc sh/sz/bj） | 新增通达信本地数据源（日线/分时文件），配置项 `TDX_PATH` |
| Q4 | 代理 `127.0.0.1:7890` 可用 | git push / uv add / 外部 API 走代理；启动代理客户端后验证 |
| Q5 | 数据源优先级与降级链按草案 | §四.3 |
| Q6 | **需要盘中模式** | 模式表扩为 6 种：`pre_market / auction / intraday_am / noon / intraday_pm / close` |
| Q7 | 聊天三档全做 | 单分析师 + 多分析师交叉 + 历史对话检索（复用 chroma） |
| Q8 | 聊天入口按 Codex 风格 | 内容区首页卡片 + 侧栏「对话记录」分组 |
| Q9 | 首页布局按 Codex 风格 | 复盘表单 + 聊天卡片 + 历史分组 |
| Q10 | 需要旧 output/ 导入脚本 | `scripts/import_output_history.py` 迁入 `data/reviews/` |

## 一、业务功能

### 1.1 前端（React + Vite + ECharts，Codex 桌面风格）
- 深色侧栏 `#17181D`、内容区 `#F7F7F5`、强调绿 `#10A37F`、圆角卡片、细边框、全中文；
- 侧栏可收起/展开（收起后 brand 图标 = 恢复入口）；「新建复盘」为侧栏唯一主按钮；
- 首页：复盘表单（日期 + 时间点模式选择）+ 聊天卡片（选标的/分析师）+ 历史分组；
- 复盘任务分步进度条（取数→资讯→分析师 n/5→主持人→辩论→报告→完成，R15 R2 节点合并；
  NEWS_ENABLED=0 时资讯阶段隐藏），轮询 job，
  禁止白屏；fetch 30s 超时 + 中文错误；上传/选择控件支持重复操作；
- 结果页：报告 Markdown + 分析师折叠卡 + 辩论记录 + 数据快照图表（ECharts 中文 tooltip）；
- **红涨绿跌**：正收益红 `#E03131` 系、负收益绿 `#0CA678` 系、零值中性灰；警示/免责声明框不参与；
- 历史按日期分组、每日一条（R11 R4；旧多份结构仅读取兼容归并展示），删除需二次确认；
- 空态/加载/错误态全覆盖；字段映射走 normalize 层 + 契约断言脚本。

### 1.2 后端（FastAPI）
- 异步任务（线程 + 进度回调 + 轮询接口）；历史接口（按日期分组每日一条 / 按日期详情与删除，
  `GET/DELETE /api/reviews/{date}`）；
- 静态托管 `frontend/dist`；`GET /api/health`；错误一律中文；参数校验 400/413/404 语义明确。

### 1.3 复盘引擎（LangGraph 保留）
- **6 种时间模式**（§二）：模式决定取数节点与提示词模板，分析师流程复用
  （并行点评 → 主持人 → 分歧 → 辩论循环 ≤3 → 汇总报告）；
- 自动注入历史上下文：昨日收盘复盘 + 当日更早时间点复盘；
- 输出：`final_report`（唯一 Markdown）、`analysts[]`、`debate_history[]`、
  `overall_tags[]`、`degraded`、`disclaimer`；
- LLM 无 Key/失败 → 规则引擎降级（`degraded=True` 可见标注）；数据缺失 → 明确标注，绝不静默。

### 1.4 分析师聊天（功能 B，三档全做）
- 选择标的（个股/板块）与分析师（单人或多人）多轮对话；
- 上下文 = skill 人格 + 标的实时/历史数据 + chroma 相关复盘结论 + 历史消息；
- 多分析师交叉问答：对上一轮回答逐位表态（同意/反对/补充）后汇总；
- 对话持久化 `data/chats/`，可按标的/日期查看历史会话，支持删除；
- 历史对话检索：chroma 新增 `chat_history` 集合（或扩展 review_history）；
- 免责声明程序级强制：回复组装层追加，响应恒含 `disclaimer`，前端固定横幅。

### 1.5 历史存储（R11 R4：每日一份，覆盖前归档）
- `data/reviews/{YYYY-MM-DD}/`：`meta.json + report.json + snapshot.json + plan.json`，
  **record_id = 日期**；同日期新复盘**覆盖写入**，覆盖前旧版完整目录归档到
  `output/archive/{YYYY-MM-DD}_{HHMMSS}/`（脚本化复制，不依赖 git）；
- 旧「同日多份 `data/reviews/{date}/{HHMMSS}/`」结构（v1 时代）已废弃：仅读取兼容
  （详情取时间点最新一份归并，record_id 统一为 `{date}`），写入不再产生多份；
- 列表按日期倒序、每日一条；详情/删除按日期（`GET/DELETE /api/reviews/{date}`，204/404 中文）；
- 复盘自动引用历史：昨日收盘复盘 + 当日更早时间点（`/api/reviews/context` 注入）；
- **旧 `output/` 导入脚本**：`scripts/import_output_history.py` 把
  `output/{date}/复盘_{time}.md` 导入为 `data/reviews/{date}/`（模式按时间点推断，
  快照缺失标注 `imported_from_legacy`），幂等可重跑，兼容新结构。

## 二、复盘时间模式定义（6 模式，Q6 确认）

| 模式 | 代码 | 适用时间 | 数据输入 | 输出 |
|---|---|---|---|---|
| 早盘前决策 | `pre_market` | 当日 09:15 前 | 隔夜外盘（纳指/富时A50）、昨夜消息、昨日/前日收盘复盘、指数日线均线 | 今日大盘预判 + 操作计划 |
| 竞价复盘 | `auction` | 09:15–09:25 | 竞价数据：高开/低开幅度、竞价金额、抢筹/砸盘、热门股竞价异动 | 早盘操作建议（能追/回避） |
| 上午盘中 | `intraday_am` | 09:30–11:30 | 上午分时（截至当前）、实时板块涨幅/资金、实时涨停/炸板、昨日涨停今日表现、早盘前决策/竞价复盘对照 | 上午盘面回顾 + 午后走势判断 + 操作计划 |
| 午间复盘 | `noon` | 11:30–13:00 | 上午分时全量、板块涨幅/资金、涨停/炸板、上午复盘对照 | 上午回顾 + 下午走势判断 + 买卖计划 |
| 下午盘中 | `intraday_pm` | 13:00–15:00 | 全天分时（截至当前）、板块资金流、涨停梯队/炸板、情绪指标、午间复盘对照 | 下午走势判断 + 尾盘策略 + 明日预案 |
| 收盘复盘 | `close` | 15:00 后（**默认**） | 全天行情：指数/涨跌停/板块/情绪/资金/龙虎榜/资讯 | 当日复盘报告 + 明日计划 |

规则：
1. **时间可用性判定**（服务端判定 + 前端提示）：所选时间点不在模式窗口内 → 中文提示并建议
   最近可用模式（如 09:40 选 auction → 提示「竞价数据已结束，建议切换上午盘中或午间复盘」；
   09:25–09:30 → 建议 auction/intraday_am；非交易日选盘中模式 → 提示非交易日）；
2. **历史日期补做**：允许任意日期补做任意模式；数据源无历史 → 对应数据块标注
   「数据缺失（数据源保留期外/仅当日）」并继续流程；
3. **每日一份（R11 R4）**：同一日期多次复盘**覆盖写入**（record_id=日期），覆盖前归档
   `output/archive/`；历史补做任意日期不受限；旧多份结构仅读取兼容；
4. 盘中模式执行建议：`intraday_am` 建议 10:00 后、`intraday_pm` 建议 14:00 前（可选，不强制）。

## 三、架构

```
backend/main.py                 # FastAPI 装配：health/静态托管/CORS（CORS_ORIGINS 环境变量化）/
                                #   中文 400 异常处理 + include_router（2026-08-13 拆路由，纯重构）
backend/jobs.py                 # JOBS 状态 + 复盘任务执行（_run_review_job）+ 进度/降级
backend/snapshot.py             # 引擎快照 → 前端契约快照（_to_frontend_snapshot）
backend/routes_review.py        # 复盘：POST /api/reviews、GET /api/jobs/*、GET/DELETE /api/reviews/*
backend/routes_chat.py          # 聊天：POST/GET/DELETE /api/chat/sessions[...] + SSE 流式透传
backend/routes_data.py          # 行情：/api/data/*（realtime/quote/stock/{code}/search/stock-names/
                                #   market-weather/us-nasdaq/sector-heat/extra-indices/
                                #   market-sentiment/market-style）+ /api/watchlist
backend/accounts_routes.py      # R12 模拟账户：GET /api/accounts、GET /api/accounts/{skill_id}
backend/trades_routes.py        # R11 交割单：GET /api/trades（列表/详情）、POST /api/trades/upload、
                                #   POST /api/trades/{id}/analyze
src/stock_review_crew/
  config.py                     # LLM + TUSHARE_TOKEN + KAIPANLA_COOKIE + TDX_PATH + NEWS_ENABLED 只读 .env
  llm_runtime.py                # R15：统一 LLM 运行时（graph/trades 两引擎复用、硬超时、降级）
  llm_sanitize.py               # R15：sanitize_for_llm 递归去股票名称（只留 6 位代码）
  state.py                      # ReviewState + ChatState + 模式字段
  graph.py                      # 6 模式感知图：fetch→news→analysts(5 位并行)→host⇄debate→report
  prompts.py                    # 6 模式提示词模板 + 主持人/复盘助手角色 + 下单格式冻结
  chat.py                       # 聊天引擎（6 步流水线/交叉问答/免责声明强制）
  quality.py                    # 质量评估：trace_review/score_review/score_chat + Langfuse rubric
  knowledge_store.py            # chroma 检索（review_history + chat_history 集合）
  accounts/                     # R12/R14 模拟账户：engine.py（校验撮合）、storage.py、plan.py
                                #   （plan.json 唯一真源）、plan_extractor.py、quotes.py（行情缓存）、
                                #   replay.py（全量重放）
  trades/                       # R11 交割单：parser.py（解析）、metrics.py（指标）、analyst.py（AI 报告）
  tools/stock_data.py           # 模式化取数/超时/降级链/竞价/分时/龙虎榜/板块历史（同花顺直连）
  tools/market_extra.py         # R10：市场天气/纳指/板块温度/额外指数/风格坐标
  tools/realtime.py             # 实时快照/个股行情/涨跌家数/快讯（A 股白名单过滤）
  tools/search.py               # R11 R9：联网搜索垂直多源聚合（东财公告+新浪7x24+盘面，无 Key）
  tools/tdx_local.py            # 通达信本地数据（F:\tdx vipdoc，只读）
  tools/tdx_remote.py           # 通达信协议直连（tdxpy：实时五档/日K/分钟K/指数K）
  tools/ths_crawler.py          # 同花顺抓取
  tools/browser_crawler.py      # 浏览器抓取（代理探测）
  storage/reviews.py            # data/reviews 读写删（每日一份+归档+旧结构兼容）+ 上下文注入
  storage/chats.py              # data/chats 会话持久化
  storage/watchlist.py          # R10 F9：data/watchlist.json 观察列表
  skills/                       # 5 份 skill.json 保留 + 新增复盘助手角色
frontend/                       # React + Vite + ECharts（4 Tab：数据展示/当日复盘/聊天/交割单）
data/reviews/  data/chats/  data/accounts/  data/watchlist.json  data/quotes_cache/
  output/archive/  data_cache/  chroma_db/
tests/  scripts/  work/  docs/
（旧 CLI 入口 src/main.py、app.py 已删除（2026-08-13）；streamlit 依赖已移除）
```

### 复盘流水线
`POST /api/reviews {date, mode, max_rounds?} → {job_id}`
→ 模式化取数（每源 ≤30s 超时、降级链、快照）→ 资讯分析（`NEWS_ENABLED=0` 时静默跳过，不发
资讯阶段）→ **分析师（5 位一次并行：趋势派 2 + 情绪派 3；R15 R2 方案 B，情绪派不再注入趋势派结论）**
→ 主持人 → 辩论（≤3 轮）→ 报告（强制免责声明）→ 落盘 `data/reviews/{date}/`（每日一份，
覆盖前归档 `output/archive/`）
前端轮询 `GET /api/jobs/{job_id}`（阶段：取数/资讯/分析师 n/5/主持人/辩论/报告/完成，
analysts_done/analysts_total=5，job 契约不变）。

### 聊天流水线
`POST /api/chat/sessions {target_type, target, analysts[], title?} → {session_id}`
`POST /api/chat/sessions/{id}/messages {content} → {messages[], disclaimer}`
上下文 = skill 人格 + 标的实时/历史数据 + chroma 相关复盘结论 + 历史消息；
交叉问答 = 逐位分析师对上一轮回答表态后汇总；每条回复强制追加免责声明。

## 四、数据源与降级链

### 4.1 数据源优先级（Q5 确认）
| 数据项 | 主源 | 备用 | 历史深度 | 备注 |
|---|---|---|---|---|
| 指数日线/均线 | 通达信 akshare | 东财日K / 腾讯日K / **通达信协议(远程主站)** / 通达信本地 | 长 | 本地可离线；**创业板+科创50 必须保障，深证成指非必须**（2026-08-04 确认：缺失时降级标注继续，不得判整块缺失）；协议源实测含指数涨跌家数 |
| 涨停/跌停池、连板梯队 | 东财 | 同花顺/Tushare 计算 | 约 30 交易日 | 超期标注缺失 |
| 板块涨幅/资金 | 同花顺 | 东财板块 | 实时+历史 | 历史逐行业慢，缓存 |
| 个股日线/情绪 | Tushare | 东财 / **通达信协议(远程主站)** / 通达信本地 | 长 | token 移入 .env；协议源解决本地 F:\tdx 陈旧问题 |
| 竞价数据 | **开盘啦（Cookie）** | 同花顺竞价页 → 东财分时 09:25 推算 | 当天/近数日 | Cookie 未提供前走降级 |
| 分时/分钟线 | 东财 | 腾讯分时（仅当日）/ **通达信协议(远程主站，1/5/15/30/60分钟K，约6个交易日)** / 通达信本地（minline） | 约 6 个交易日（协议源） | 超期标注缺失；历史分钟补做已实测可用 |
| 个股实时行情 | 东财 push2 | 腾讯行情 / **通达信协议(远程主站，五档)** | 实时 | 东财/腾讯双挂时协议源兜底（2026-08-04 接入） |
| 龙虎榜 | 东财 | Tushare top_list | 长 | 新增 |
| 隔夜外盘（纳指/A50） | 新浪/东财外盘 | — | 仅当天 | 历史补做标注缺失 |
| 财经资讯 | 财新（当天）/央视/经济日历 | — | 财新仅当天 | 同上；**开关 `NEWS_ENABLED`（默认关闭=屏蔽，`1/true/on/yes` 开启，2026-08-12）** |
| 全市场涨跌分布/炸板率 | Tushare 计算 | 东财 | 长 | — |

> **资讯开关（2026-08-12）**：环境变量 `NEWS_ENABLED`，**默认关闭**——屏蔽全部资讯
> 内容：复盘不取财新/央视、报告不含「今日财经资讯/资讯分析」章节；聊天 `news`/
> `news_headlines`/`realtime_news` 三键返回「资讯已关闭」；实时快照（新浪7x24）
> 返回空列表（前端「最新快讯」自动隐藏）。设 `NEWS_ENABLED=1`（或 `true/on/yes`）
> 后重启服务恢复资讯。此开关不影响「联网搜索简报」（用户问题相关时才请求）。

> 北向资金实时数据 2024-08 起停发，**不作为指标**（以涨跌家数/昨日涨停表现/炸板率替代）。

### 4.2 降级链约定
`主源 → 备用源 → 本地缓存 → 明确标注「数据缺失/估算」`；每个数据块携带 `source` 与
`degraded` 标记；任何降级必须进入报告/快照可见标注区，禁止静默给错数据。

### 4.3 所需凭据（均只入 `.env`，不进 git）
- `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL`（已有）；
- `TUSHARE_TOKEN`（从 config.py 硬编码移入，Q2）；
- `KAIPANLA_COOKIE`（Q1，用户提供后填入；未填走降级）；
- `TDX_PATH=F:\tdx`（Q3）；
- 代理 `127.0.0.1:7890`（Q4，git push/uv add/外网 API）。

### 4.4 开盘啦 Cookie 提供方法（Q1）
1. 用 Chrome/Edge 打开 https://www.kaipanla.com 并登录账号；
2. F12 → Network 面板 → 刷新页面 → 任选一个 kaipanla.com 的请求（如 `Index/index`）；
3. 复制该请求 Headers 里的完整 `Cookie:` 值；
4. 粘贴到 `H:\stock_review_crew\.env`：`KAIPANLA_COOKIE=粘贴值`（含引号则去掉引号）；
5. Cookie 可能过期，失效后重抓即可；数据层每次请求自动带该 Cookie 并做失败降级。

## 五、API 契约（前端依赖，冻结）

> 约定：比率一律**小数**（0.5=50%）；金额单位元；日期 `YYYY-MM-DD`；时间点 `HHMMSS`；
> `None` = 无数据（前端显示「—」，禁止 0）；前端负责全部格式化（×100/中文）；
> 所有投资相关内容响应恒含 `disclaimer` 字段（后端程序级强制）。

```
GET  /api/health                      → {status:"ok", langfuse:{enabled, callback_ok, host, mode},
                                         news_enabled}   # 健康检查（R10 附 Langfuse 状态；R15 附资讯开关）
POST /api/reviews       Body:{date, mode, max_rounds?} → {job_id}   # 发起复盘（日期/模式/窗口校验，中文 400）
GET  /api/jobs/{job_id} → {job_id, status(queued|running|done|error), stage, pct, message,
     analysts_done, analysts_total, result|null, error|null, quality?}   # 任务轮询
     result = {record_id, meta, report, snapshot}；record_id = 日期（R11 R4）
     meta = {date, mode, mode_label, created_at, degraded[], disclaimer, summary, accounts?}
     quality = {score, breakdown, passed, trace_id}（R10 Q4，可选摘要）
GET  /api/reviews       → [{date, items:[{record_id, mode, mode_label, time, created_at, summary}]}]
                         # 历史分组：每日一条、record_id=日期（旧多份结构归并取最新一份）
GET  /api/reviews/status?date= → {date, is_trading_day, latest_date, latest_mode, has_today_review,
     can_auto, pending_job_id}   # R11 R2 自动复盘状态（15:00 后无复盘 → 前端自动发起 close 复盘）
GET  /api/reviews/{date} → {meta, report, analyses[], debate_history[], snapshot}   # 单日详情（每日一份）
DELETE /api/reviews/{date} → 204 / 404（中文）   # 删除单日复盘
GET  /api/reviews/context?date=&mode= → {yesterday, earlier_today[]}   # 历史上下文注入（昨日收盘+当日更早）
POST /api/chat/sessions Body:{target_type:stock|sector, target, analysts:[...], title?} → {session_id}
     # 创建聊天会话（多分析师交叉问答，会话持久化 data/chats/）
POST /api/chat/sessions/{id}/messages Body:{content} → {session_id, messages[], disclaimer}
     # 发送消息（6 步流水线：解析→数据需求→取数→分析→综合总结）
POST /api/chat/sessions/{id}/messages/stream → SSE   # 流式（R8 v4 §五 事件契约：stage/analyst_*/
     #   summary_delta*/done/error；SSE 断开/worker 异常落盘「回复中断」消息，前端轮询可认领）
GET  /api/chat/sessions?target=&date= → 会话列表   # 按标的/日期查看历史会话
GET  /api/chat/sessions/{id} → {meta, messages[], disclaimer}   # 会话详情回看
DELETE /api/chat/sessions/{id} → 204 / 404（中文）   # 删除会话
GET  /api/accounts       → {accounts:[{skill_id, name, cash, total_assets, total_return_rate,
     unrealized_pnl, realized_pnl, win_rate, profit_loss_ratio, ...}], disclaimer}
                         # R12 全部分析师模拟账户摘要（按 skills 遍历，缺失自动开户）
GET  /api/accounts/{skill_id} → 单人完整账户（含 positions/closed_positions/trades/snapshots）   # R12
GET  /api/trades         → 历史交割单列表（trade_id/时间/记录数/has_analysis/摘要，倒序）   # R11/R12
POST /api/trades/upload  (multipart) → {trade_id, records, metrics, summary}   # 上传解析（≤10MB，中文 400）
GET  /api/trades/{id}    → {records, metrics}（回读 analysis.json 历史报告不重新生成）   # 交割单详情
POST /api/trades/{id}/analyze → {report, disclaimer}   # 异步分析（LLM 不可用 → 规则引擎降级，绝不 500）
GET  /api/data/realtime  → 盘中实时快照（指数/涨跌停统计/板块资金流/快讯，各块独立降级）   # R9 升级
GET  /api/data/quote?code= → 个股实时行情（五档/涨跌幅等，只读消费，观察列表复用）   # 既有行情链
GET  /api/data/stock/{code} → 个股详情聚合（实时/日K60/分时/5分钟/1分钟K/MA5-20，各块独立降级）   # R9
GET  /api/data/search?q= → 股票搜索（代码或名称，归一 6 位代码）   # 首页搜索/添加自选
POST /api/data/stock-names Body:{codes:[...]} → {names:{code:name|null}}   # R15 M5 批量代码→名称
GET  /api/data/market-weather → 市场天气/情绪温度（weather/emotion_score/reasons）   # R10 F3 P0
GET  /api/data/us-nasdaq → 纳斯达克综合 + 纳指100（近 60 日走势，两指数独立降级）   # R10 F6 P0
GET  /api/data/sector-heat → 板块热度榜（涨幅 top5/跌幅 top5 + 领涨股）   # R10 F5
GET  /api/data/extra-indices → 平均股价/微盘股/高股息 三指数   # R10 F2
GET  /api/data/market-sentiment → 统一情绪指标（涨跌家数比/炸板率/连板高度等，60s 缓存）   # R12
GET  /api/data/market-style → 风格坐标（大盘vs小盘/价值vs成长，0-100 + 标签）   # R10 F4
GET  /api/watchlist      → [{code, name, note, pinned, created_at}]   # R10 F9 观察列表
POST /api/watchlist      Body:{code, note?} → 200 记录（重复 409 中文）   # 添加自选（名称解析失败不阻塞）
DELETE /api/watchlist/{code} → 204 / 404 中文   # 删除自选
PUT  /api/watchlist/order Body:{codes:[...]} → 200 重排后完整列表   # 置顶排序（缺失/非法代码忽略）
```

前端 normalize 层严格对照本契约，配契约断言脚本（参考 synalysis `verify-normalize.mjs`）。

## 六、数据字典约定（口径铁律）

1. 比率一律小数存储；上游百分比数值（如 `pct_change=9.98`）进契约前 `/100`，
   快照 raw 保留原值并标注单位；
2. None = 无数据，前端「—」禁 0；非有限数输出 None；
3. 红涨绿跌：正红 `#E03131`、负绿 `#0CA678`、零中性灰；警示/免责声明框不参与；
4. 中文展示：月份「2025年11月」、日期「2025-11-27」、状态/来源中文；
5. 情绪指标口径（写死）：昨日涨停今日平均收益/红盘率/连板率/核按钮率；
   炸板率 = 炸板数 ÷ 摸过涨停数；涨跌家数比；涨停按板块差异化
   （主板 10%、创业板/科创板 20%），过滤 ST/北证；
6. 竞价指标口径（数据源确认后冻结）：高开幅度 = (竞价参考价 − 昨收) ÷ 昨收；
   竞价金额 = 9:25 集合竞价成交额；抢筹/砸盘 = 9:20–9:25 委托净流入方向；
   异动榜 = 竞价涨跌幅/金额排名前列；
7. 免责声明 `"仅供参考，不构成投资建议"`：报告与聊天回复在组装层强制追加，
   响应恒含 `disclaimer` 字段；前端固定横幅；
8. 降级链：主源→备用→缓存→标注缺失；LLM 无 Key→规则引擎（degraded=True 可见）；
   历史缺失明确说明，禁止编造；
9. 边界：每源超时 ≤30s、LLM 超时 120s、任务总时长上限、磁盘写入失败不阻塞主流程。
10. **模拟账户口径（R12，2026-08-08 确认冻结）**：
    - 每位分析师（按 skill_id 动态开户，含未来新增）独立账户，初始资金 **100,000 元**；
      落盘 `data/accounts/{skill_id}/`：account.json（现金/持仓/冻结）、trades.json
      （成交+拒绝全记录）、snapshots.json（每日资产快照）；
    - **T+1**：当日买入当日不可卖（frozen_qty 冻结，跨日结算解冻）；
    - **涨跌停**：成交价必须在 [跌停价, 涨停价]（主板 ±10%、创业板/科创板 ±20%，
      按前收盘价×系数四舍五入 0.01）；收盘涨停买单拒绝、收盘跌停卖单拒绝；
    - **数量**：100 股整数倍；**科创板（688/689）最低 200 股（2 手）起、100 股递增**
      （2026-08-13 修复：买入不再递减到 100 股成交）；卖出 100 股整数倍，零股允许整仓一次性卖出；
    - **费用**：佣金 万2.5（最低 5 元，买卖双向）、过户费 0.001%（双向）、印花税 卖出 0.05%；
    - **撮合**：分析师下单参数 = 代码 + 方向 + 目标价 + 仓位成数；当日价格区间覆盖目标价
      （最低 ≤ 目标价 ≤ 最高）即按目标价成交，数量 = floor(可用现金×成数/10/(目标价×1.001)/100)×100；
      **target ≥ 涨停价 → 买单拒绝；target ≤ 跌停价 → 卖单拒绝；当日区间未触及 → 拒绝**
      （拒绝原因中文记录，逐笔含 balance_after）；
    - 规则引擎（LLM 不可用）不产生新交易（账户只读快照，避免乱下单）；
    - 合规清单（R12-3）：超资金 / T+1 / 涨跌停越界 / 股数非整百 / 裸卖空，引擎层拒绝即拦截成功。
11. **plan.json 数据字典（R14，`data/reviews/{date}/plan.json` 唯一真源）**：
    - 顶层 `{date, mode, generated_at, plans[]}`；mode 固定 "close"（批跑仅收盘模式）；
      plans[] 可为空数组（空 = 该日无推荐）；
    - plans[] 字段：`rec_date`（=date）、`skill_id`（排除 host/review_assistant）、`skill_name`、
      `code`（6 位数字 zfill(6)）、`name`（缺失用 code）、`side`（buy/sell）、
      `target_price`（>0 元）、`position_cheng`（1-10，0=未用成数）、`qty`（>0 整数，null=按成数折算）、
      `source`（枚举：tool | regex_qty | regex_cheng）；
    - **去重键 (rec_date, skill_id, code, side, target_price)，target_price 容差 <0.01**；
      同日重跑复盘整文件覆盖、无残留旧计划；账户批只读 plan.json，不再做任何正则/文本解析。
12. **下单格式冻结（R15）**：分析师推荐统一为 `代码：买入价XX.XX元，仓位X成`
    （卖出：`卖出 代码：卖出价XX.XX元，仓位X成`）——**只写 6 位股票代码，禁止写股票名称**
    （名称会引发代码-名称不匹配下单失败）；每行一只；不选 ST/退市/北证（8 开头）股。
13. **资讯开关 NEWS_ENABLED（2026-08-12）**：环境变量**默认关闭**（屏蔽全部资讯），
    `1/true/on/yes` 开启；关闭时复盘不取资讯、报告无「资讯分析」章节、聊天 news 三键返回
    「资讯已关闭」、实时快讯返回空列表；`/api/health` 返回 `news_enabled` 供前端隐藏资讯阶段；
    不影响「联网搜索简报」（用户问题相关时才请求）。

## 七、任务分配与文件所有权（并发 ≤5）

| Issue | 模块 | 唯一写权限（草案） | 只读契约 |
|---|---|---|---|
| I1 | 数据层：模式化取数/超时/降级链/竞价/分时/龙虎榜/通达信本地/开盘啦 Cookie | `src/stock_review_crew/tools/**`、`tests/test_data*.py` | requirements.md §二/§四/§六 |
| I2 | 复盘引擎：6 模式 LangGraph/提示词/主持人/助手角色/进度钩子/降级 | `src/stock_review_crew/{graph,state,prompts}.py`、`skills/**`、`tests/test_graph*.py` | §二/§六 + I1 工具契约 |
| I3 | 聊天引擎：会话/交叉问答/检索/免责声明 | `src/stock_review_crew/chat.py`、`storage/chats.py`、`tests/test_chat*.py` | §五 + skills |
| I4 | 历史存储：reviews 读写删/上下文注入/旧 output 导入脚本 | `storage/reviews.py`、`scripts/import_output_history.py`、`tests/test_storage*.py` | §一.5/§五 |
| I5 | 后端 API：jobs/reviews/chats/静态托管/中文错误 | `backend/**`、`tests/test_backend*.py` | §五 + I1-I4 契约 |
| I6 | 前端：React/Vite/ECharts/Codex 风格/进度/聊天/历史分组/红涨绿跌 | `frontend/**`（npm 依赖可装） | §五（开发期 mock 对齐） |
| R1（Wave 3） | 全量业务逻辑审计 | 只读，输出审计报告 | 全部代码 + 本文档 |

规则：禁止 git、禁止改他人文件、禁止真实数据入库、`python -m pytest` 用 `.venv` Python
（前端 `npm run build` 验证）；发现问题写入报告由主 agent 派发修复；模块负责人完成后
**保留待命**，登记表写入 `docs/PROGRESS.md`，后续修改 resume 复用。

## 八、流程
1. ✅ 需求确认（本文档）
2. 创建 GitHub issues（I1-I6 + R1，含验收标准与所有权表）→ 首提基线（补 .gitignore）
3. Wave 1：I1 + I2 + I3 + I4 + I6 并行（5）
4. Wave 2：I5 后端集成 + 主 agent 集成审查（全量 pytest、npm build、启动冒烟）
5. Wave 3：R1 只读审计 → 派发修复
6. 冒烟：6 模式真实数据各至少 1 次（今日午间窗口 noon；其余近期交易日补做，
   盘中模式在窗口内执行或标注降级）→ 验收交付（PROGRESS.md、README、提交推送）

## 九、风险（承接草案 §十，增量）
- R1 竞价数据依赖开盘啦 Cookie（未提供前降级实现）；R2 东财涨停池 30 交易日限制；
  R3 分时历史深度待实测；R5 token 迁移后须验证可用；R6 代理需启动客户端后验证；
  R11 通达信本地文件格式解析（.day/.lc1/.lc5）工作量可控但需隔离测试（只读、不写回）；
  R12 6 模式提示词与冒烟工作量增大，冒烟按窗口灵活排期。

## 十、R8-R15 增量变更摘要（2026-08-13 合并登记）

> 各轮完整口径见对应增量文档（文件保留供追溯，头部已标「已合并」）；下表为交付结论速览。
> 冲突时以更新版本为准（r15 > r14 > … > v4），字段真源以本文 §五/§六 为准。

| 版本 | 主题 | 关键结论 | 文档 |
|---|---|---|---|
| v4 | R8 聊天智能流程（6 步流水线） | 问题解析→数据需求→取数→分析→综合总结→流式输出；取数失败本轮不回答、如实告知缺失项；Q8 简报流式展开、Q9 在途轮次完整性（轮询认领）、Q10 多会话并发分桶；SSE stage 事件契约 | requirements-v4.md |
| r10 | 今日数据扩展：市场天气/纳指/板块温度/额外指数/风格坐标/观察列表/设置页 | F3 市场天气 P0（base50+5 项手算评分冻结）；F6 纳斯达克 P0；Q4 Langfuse 质量打分硬需求（trace/score 写回，复盘/聊天均评）；AKShare 探测结论冻结；F12 设置页纯前端（主题/红绿配色/图表周期/字号） | requirements-r10.md |
| r11 | 首页 4 Tab 重构 + 自动复盘 + 每日一份存储 + 聊天搜索流程 | 每日一份 `data/reviews/{date}/`（覆盖前归档 output/archive/，record_id=日期）；GET /api/reviews/status 自动复盘；R9 搜索改垂直多源聚合（博查实测质量不可用、弃作默认源）；R8 快讯 A 股白名单；R6 禁比喻表述铁律；R10 在途标记 sessionStorage 恢复 | requirements-r11.md |
| r12 | 分析师模拟账户系统 | 初始 10 万/人动态开户；T+1/涨跌停/整百/费用冻结；目标价撮合（≥涨停拒买、≤跌停拒卖、区间未触及拒）；回测 fixture 5 人各 29 笔成交 0 违规、引擎漏洞 0；GET /api/accounts 增量接口 | requirements-r12.md |
| r13 | 交割单「解析明细」展示优化 | 解析明细去掉银行转账等非交易操作（BANK_TO_SEC 等 8 类不展示）；按股票汇总展示（买0卖N=期初持仓卖出、买N卖0=持仓中未卖出、closed=已平仓/held=持仓中）；仅前端 TradesTab，后端零改动 | requirements-r13.md |
| r14 | 复盘批次 × 账户批次两段式跑批 | plan.json 唯一真源 + 账户全量重放（确定性、不调 LLM）+ quotes_cache 行情缓存；work/review_batch.py + work/account_replay.py；删除 scripts/backtest_accounts.py（合规断言并入 replay）；历史复盘不迁移（校验后重跑） | requirements-r14.md |
| r15 | LLM 脱敏 / 分析师节点合并 / 指数成交额回填 / 空观点修复 / 名称转回 | llm_sanitize.sanitize_for_llm（只留 6 位代码）；trend+sentiment 合并为 analysts 节点（方案 B，情绪派不注入 trend_context）；上证/创业板/科创50 amount 回填；空话检测兜底；POST /api/data/stock-names 批量名称转回；下单格式冻结 `代码：买入价XX.XX元，仓位X成` | requirements-r15.md |
