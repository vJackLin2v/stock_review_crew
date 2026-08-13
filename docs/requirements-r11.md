<!-- 已合并进 requirements.md，本文档保留供追溯，勿再新增改动（2026-08-13） -->
# R11 首页重构与体验改造 — 需求分析文档（草案，待用户逐条确认）

> 2026-08-08 提出。本文为**草案**：方案与口径待确认后才进入开发（multi-agent-dev Phase 0）。
> 铁律不变：比率小数存储/前端 ×100、None 显示「—」、全中文、免责声明恒在、改动后必须一键回测。

## 一、需求逐条落地方案

### R1 首页按功能拆 Tabs
首页（HomeView）改为 4 个 Tab：

| Tab | 内容（迁移自现有 HomeView 模块） |
|---|---|
| 当日复盘（推荐默认） | 自动复盘展示 + 报告阅读 + 历史复盘列表（原「新建复盘」表单与历史分组移入/替换） |
| 数据展示 | 今日数据大卡（指数/涨跌停/资金流/快讯/个股行情）+ 天气/纳指/板块温度/风格坐标 + 观察列表 |
| 分析师聊天 | 嵌入现有 ChatView（保持挂载或恢复机制，见 R10） |
| 交割单分析 | 新增：上传交割单 → 解析表格 → 指标（TWR/胜率/盈亏比等）→ AI 行为画像/报告 |

改造点：`App.jsx` 的 home 视图内做 tab 状态；ChatView 从独立 view 迁入 tab（App 仍保留 chat view 兼容
侧栏入口）；`HomeView.jsx` 拆为 `ReviewTab` / `DataTab` / `ChatTab` / `TradesTab` 或同类组件。

### R2 自动复盘（去掉新建复盘）
- 后端新增 `GET /api/reviews/status?date=`：返回 `{date, latest_review_date, has_review, mode, in_progress}`；
  语义：15:00 前 → 展示最近交易日（周一 15:00 前 = 上周五）复盘；15:00 后且当天无复盘 → 自动发起任务。
- 前端打开首页（当日复盘 tab）时调用 status：无复盘且已过 15:00 → 自动 `POST /api/reviews`（mode=close）
  并发起进度展示；当天已有复盘（含进行中 job）→ 直接展示，**不重复触发**。
- 非交易日：展示最近交易日复盘（复用历史补做能力）。
- 移除首页「新建复盘」表单（日期/模式/轮数选择 UI），入口保留在侧栏/设置（手动补做历史场景）。
- 自动复盘触发条件如需「仅收盘后 15:00」严格化，可在后端判定（避免前端时钟差异）。

### R3 去掉数据缺失/来源提示
- 前端不再渲染 `degraded-banner`、降级红字、「· 东财实时」等来源文本；无数据显示「—」（已有 null 判断
  helper 复用）；图表空态不再写「暂无XX数据」。
- **与铁律 7 的冲突处理（待确认）**：建议 UI 隐藏、但后端降级数据与「—」tooltip 保留来源信息
  （鼠标悬停可见），保证「绝不静默错报」底线不破。

### R4 每日一份复盘 + 旧记录归档 output/
- 存储改为 `data/reviews/{YYYY-MM-DD}/`（去掉 HHMMSS 层级），同日期新复盘**覆盖写入**；
- 覆盖前把旧版完整目录（meta/report/snapshot）归档到 `output/archive/{YYYY-MM-DD}_{HHMMSS}/`
  （输出目录=用户可查看的保留区，脚本化复制，不依赖 git）；
- API 变更：`GET /api/reviews` 分组保持日期维度（每日一条）；`GET /api/reviews/{date}` 单条；
  `DELETE /api/reviews/{date}`；同日多份逻辑（时间点）删除；历史导入脚本（import_output_history）兼容。

### R5 复盘目录优化（sticky）
- `MarkdownView` 目录改为**固定定位**：桌面端右侧/顶部 sticky（滚动时可见），移动端顶部横向胶囊；
  点击锚点滚动保留；「返回顶部」保留。目录随滚动高亮当前章节（可选）。

### R6 报告表述约束（去掉「近乎腰斩」类）
- `build_report_prompt` 增加表述铁律：**禁止比喻/夸张/口语化修辞**（如「近乎腰斩」「断崖」「崩了」），
  一律用数据+百分比对照表达（如「昨日涨停红盘率 53.57%，较前日 76.92% 下降 23.35 个百分点」）；
  quality 校验增加敏感表述关键词检测（可选 P2）。
- 报告章节结构、格式**完全不变**（R7）。

### R7 复盘格式不变
- 仅做 R6 表述约束；章节、表格、免责声明、落盘结构均不修改。

### R8 快讯只保留股市相关
- 后端快讯（新浪 7x24）返回前按 A 股相关关键词过滤：板块/个股/指数/政策/宏观/资金/外盘/公司公告等
  白名单主题；剔除体育/娱乐/社会/国际无关/广告。前端不再二次过滤。

### R9 分析师聊天流程改：搜索 → 整理 → 分析师 → 数据 → 分析
- 新流程（chat.py `_run_pipeline` 重构）：
  1. **联网搜索**：用户问题关键词 → 搜索 API（**待确认用哪家**，见 Q5）→ 取 Top N 结果；
  2. **整理**：LLM 把搜索结果整理为结构化简报（事件/影响方向/相关板块个股，200 字内）；
  3. **给分析师**：简报注入每位分析师上下文（与现有市场数据并列）；
  4. **要数据**：分析师按 need 机制请求具体数据（现有 need→fetch 复用）；
  5. **分析**：分析师基于「搜索结果+数据」作答 → 汇总（现有流程）。
- 搜索失败/无 key：降级跳过搜索步骤并标注「未联网搜索」，不阻塞分析。

### R10 聊天提问丢失问题（分析结论）
现状：已有「切走再回来」轮询恢复（ChatView :390-433，依赖 api.js 模块级 streaming 标记 +
轮询会话直到落盘）。**仍丢失的场景与根因**：
1. **整页刷新/浏览器后台回收**（手机切屏内存紧张）→ 模块级标记丢失 → 不触发轮询 → 提问不可见；
2. 后端 worker **异常终止且未落盘**（如 LLM 网络断开）→ 轮询永远等不到 done（10 次失败后放弃）；
3. 本轮 Tabs 改造若条件卸载 ChatView → rounds 内存态丢失（恢复机制可兜底，但展开动画/阶段提示会丢）。
修复方案：
- 后端：SSE 断开/worker 异常时**落盘一条中文说明消息**（「回复中断，可重试」），轮询判定
  「最后一条 user 消息无后续 assistant 且超过阈值」→ 视为中断并提示重试；
- 前端：在途标记（session_id 集合）写入 `sessionStorage`，挂载时恢复；轮询判定改为后端字段
  （如 meta.pending_round）而非仅本地标记；
- Tabs：ChatView 用 `display:none` 保持挂载（推荐）或完整恢复机制兜底。

## 二、口径假设与待确认问题

| # | 问题 | 我的推荐 | 需确认 |
|---|---|---|---|
| Q1 | Tabs 顺序与默认 Tab | **已确认**：数据展示 \| 当日复盘 \| 分析师聊天 \| 交割单分析；默认「数据展示」 | ✅ |
| Q2 | 自动复盘触发主体 | 前端打开页面检测 + 自动 POST（后端提供 status 接口）；当天只触发一次 | 确认 |
| Q3 | 需求 3 与铁律 7 冲突 | **已确认**：UI 隐藏降级横幅/source 文本，无数据显示 —；tooltip 保留来源信息（不静默） | ✅ |
| Q4 | 归档位置 | `output/archive/{date}_{HHMMSS}/`（每日覆盖前归档） | 确认 |
| Q5 | 联网搜索服务 | **已定案（2026-08-08 实测）**：博查免费档质量不可用（结果与查询无关），改为**垂直多源聚合**：东财公告接口（np-anotice-stock，无 Key，已实测）＋ 新浪7x24快讯关键词过滤 ＋ 盘面数据；博查 Key 保留为可选全网补充开关（默认关） | ✅ |
| Q6 | 交割单分析迁移范围 | 全量迁移 synalysis v2（解析/TWR 指标/AI 报告），样式按本项目主题适配 | 确认 |
| Q7 | 快讯过滤粒度 | 后端关键词白名单过滤 | 确认 |
| Q8 | 目录形式 | 桌面右侧 sticky + 移动端顶部胶囊 | 确认 |
| Q9 | 复盘覆盖触发 | 每天 15:00 后首次打开页面自动触发；手动补做入口保留在侧栏 | 确认 |
| Q10 | 分析聊天 R9 是否保留「多分析师」模式 | 保留，搜索简报注入全员 | 确认 |

## 三、架构与文件所有权草案（确认后生效）

| 模块 | 写权限（草案） | 说明 |
|---|---|---|
| T1 前端 Tabs 重构 | frontend/src/App.jsx、components/HomeView.jsx（拆 tab）、新 ReviewTab/DataTab/ChatTab/TradesTab、styles.css | R1/R2 前端/R3/R5/R8 前端 |
| T2 自动复盘后端 | backend/main.py、src/.../storage/reviews.py、scripts/import_output_history.py | R2/R4 API 与存储 |
| T3 聊天流程改造 | src/.../chat.py、prompts.py（搜索简报注入）、新 tools/search.py | R9/R10 后端 |
| T4 交割单分析迁移 | 新 src/.../trades/（parser/metrics/analyst 移植）、backend 新路由、前端 TradesTab | R1 交割单 |
| T5 表述与快讯约束 | prompts.py、src/.../tools/realtime.py（快讯过滤）、quality.py | R6/R8 |
| T6 测试与回测 | 对应 tests/、scripts/regression.py 补断言 | 全程 |

## 四、API 契约草案（新增/变更）
```
GET  /api/reviews/status?date=          → {date, is_trading_day, latest_date, latest_mode,
                                           has_today_review, pending_job_id|null}
GET  /api/reviews/{date}                → 单日复盘（覆盖后）；同日多份接口废弃
DELETE /api/reviews/{date}              → 204/404 中文
POST /api/trades/upload  (multipart)    → {trade_id, records, metrics 摘要}
GET  /api/trades/{trade_id}             → {records, metrics, analysis?}
POST /api/trades/{trade_id}/analyze     → 异步任务（复用 jobs 轮询）→ {report, disclaimer}
GET  /api/data/news?filter=a_share      → 快讯（已过滤）
```
交割单字段契约沿用 synalysis docs/requirements.md §4（TradeRecord：code/name/op_type/qty/price/
fee/transfer_fee/balance/cash_amount/contract_no 脱敏）。

## 五、风险
- 自动复盘依赖 LLM 可用（无 Key → 规则引擎降级报告，仍会落盘并展示）；
- R4 存储结构变更影响历史数据（现有 data/reviews 多份结构需迁移脚本）；现有同日多份记录
  保留只读兼容或迁移为每日一份 + 归档；
- 搜索 API Key 未提供时 R9 只能降级（流程保留但无搜索结果）；
- Tabs 重构回归面大（首页全量组件），必须跑一键回测（桌面+手机冒烟）；
- 需求 3 与铁律 7 冲突需用户拍板，否则开发中途返工。

## 七、博查接入约定（Q5 定案，2026-08-08）
**实测结论（2026-08-08，用户 Key 已提供）**：
- 博查 API 认证通过、配额可用，但**搜索质量不可用**：多组 query（含代码/日期/时效/站点限定参数）
  返回结果与查询无关（福彩字谜/彩票/政府年鉴/SEO 垃圾站），answer 模式把「7/8」当数学题。
  → 博查不设为默认搜索源；`BOCHA_API_KEY` 保留为可选开关（默认关），结果需相关性校验。
- **搜索层 v1 = 垂直多源聚合（无 Key）**：
  1. `np-anotice-stock.eastmoney.com/api/security/ann`：按股票代码拉公告（实测哈药股份 600664
     15 条：8-04 药品集采拟中选公告、7-14/17/24 异常波动公告，质量高、不受行情域 502 影响）；
  2. 新浪 7x24 快讯（复用 realtime 新闻链路）按 query 关键词过滤；
  3. 盘面数据（行情/龙虎榜/涨跌停池，已有）交叉验证；
  4. LLM 把「公告+快讯+盘面」整理为 ≤200 字结构化简报注入分析师上下文；
  5. 全部失败 → 标注「未联网搜索」继续分析，不阻塞聊天。
- 已实测不可用并排除：东财 search-api-web（hits=0）、F10 NewsBulletin（空）、np-listapi（空）、
  新浪个股新闻页（JS 渲染）、腾讯 r.inews getSimpleSearch（404）、cn.bing 无 Cookie（无结果页）、
  akshare stock_news_em（1.18.81 在 pandas 3.0 下 ArrowInvalid 正则 bug）。

## 六、流程要求（用户要求 1/2，写入 PLAYBOOK）
1. 开发与修复过程中遇到的问题按模板记录到 PLAYBOOK §8；后续任何 Bug 修复必须回归相关用例；
2. 单问题连续排查超过 10 分钟无解 → 停止重试，搜索解决方案（文档/官方源）后再继续。
