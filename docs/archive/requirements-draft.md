# Stock Review Crew 全量重构 — 需求分析文档（草案 v0.1，2026-08-03）

> 状态：**已被 `docs/requirements.md`（v1 确认版）取代**，本文仅作草案存档。
> 确认差异：新增盘中模式（6 模式）、通达信 F:\tdx 本地源、旧 output/ 导入脚本、
> 开盘啦 Cookie 接入（KAIPANLA_COOKIE）、Tushare token 移入 .env。
> 参考方法论来源：`H:\synalysis_crew`（requirements-v2.md / PROGRESS.md / metrics.py / frontend）。
> 目标仓库：[xiaoLin12130/stock_crew.git](https://github.com/xiaoLin12130/stock_crew.git)（当前本地无 remote、无 commit）。

---

## 一、现状盘点（可复用资产）

### 1.1 stock_review_crew（被重构方）
| 资产 | 现状 | 处置 |
|---|---|---|
| `skills/{alang,bingchuan,baxiaoxian,yangjia,tiechui}/skill.json` | 5 位分析师人格/体系（趋势：阿狼、爱在冰川；情绪：拔小弦、炒股养家、铁锤） | **原样复用**，补充「主持人/复盘助手」角色 |
| `src/.../graph.py` | LangGraph：fetch→news→trend→sentiment→host→debate(循环)→report，节点内线程池并行 | 重构为**模式感知**的图，保留辩论循环与进度钩子 |
| `src/.../tools/stock_data.py` | 7 个 AKShare/Tushare 工具，带 `data_cache/` 缓存 | 重写：加**超时 + 降级链 + 模式化取数 + 竞价/分时/龙虎榜** |
| `src/.../knowledge_store.py` | ChromaDB + ONNX 嵌入，历史复盘检索 | 保留，扩展为「复盘结论 + 聊天历史」双集合 |
| `app.py`（Streamlit） | 现有前端 | **废弃**，替换为 React 前端 |
| `output/{date}/复盘_{HHMMSS}.md` | 历史报告（2026-07-17~07-31，含同日多份） | 迁移为 `data/reviews/` 结构（见 §七） |
| `.env` | DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL（opencode 网关）/ Langfuse | 保留；**TUSHARE_TOKEN 缺失**（config.py 有硬编码兜底，见风险 R5） |
| `.venv`（Python 3.12.11） | akshare/tushare/langgraph/langchain-openai/uvicorn/chromadb/streamlit/openpyxl/python-multipart 已装 | 缺 **fastapi、pytest**，开发期 `uv add`（需网络，走代理） |
| `.gitignore` | 仅覆盖 pycache/venv/build | 需扩展：`data/`、`data_cache/`、`output/`、`chroma_db/`、`.env`、`node_modules/`、`dist/`、`.tmp/` |
| git | 无 remote、无 commit（master 全未跟踪） | 先补 `.gitignore` 再首提，避免真实数据/凭据入库 |

### 1.2 synalysis_crew（方法论与代码模板来源）
| 资产 | 复用方式 |
|---|---|
| `docs/requirements-v2.md` 数据字典（第六节） | 直接作为**口径与契约写法范本**（比率小数、None→「—」、红涨绿跌、字段唯一真源） |
| `docs/PROGRESS.md` agent 负责人登记表 | 直接复制该模式到本项目 |
| `backend/main.py` | 模板：异步任务 + JOBS 轮询 + TTL + 中文错误 + 静态托管 + 公网无历史模式 |
| `src/synalysis_crew/analyst.py` | 已通过 `STOCK_REVIEW_CREW_SKILLS_DIR` 复用本项目 skills；其 DISCLAIMER 程序级强制、规则引擎降级、画像脱敏逻辑**直接借鉴** |
| `src/synalysis_crew/graph.py` | 进度回调 `_emit(stage, pct, message)` + 无 Key 规则引擎降级模式 |
| `frontend/src/*`（App/Sidebar/UploadView/ProgressOverlay/Dashboard/Charts/MarkdownView + styles.css） | **整体作为前端脚手架**：侧栏收起/恢复、分步进度条、ECharts 封装、normalize 层、mock 降级 |
| `tests/test_backend.py` / `test_metrics.py` | 测试写法范本（TestClient 全链路、隔离目录、手算用例） |

---

## 二、功能点清单

### F1 前端（React + Vite + ECharts，Codex 桌面风格）
- 深色侧栏 `#17181D`、内容区 `#F7F7F5`、强调绿 `#10A37F`、圆角卡片、细边框、全中文；
- 侧栏可收起/展开（收起后 brand 图标 = 恢复入口）；**「新建复盘」为侧栏唯一主按钮**；
- 聊天入口放内容区首页卡片（侧栏唯一主按钮约束下），侧栏历史分「复盘记录 / 对话记录」两组；
- 复盘任务：模式选择（日期 + 时间点）→ 提交 → **分步进度条**（取数→资讯→趋势派→情绪派→主持人→辩论→报告→完成），轮询 job，禁止白屏；fetch 全部带超时（30s）与中文错误；
- 结果页：报告 Markdown + 分析师折叠卡 + 辩论记录 + 数据快照图表（指数分时/涨跌停梯队/板块/情绪曲线，ECharts 中文 tooltip）；
- **红涨绿跌**：正收益红 `#E03131` 系、负收益绿 `#0CA678` 系、零值中性灰；警示/免责声明框不参与；
- 历史按**日期分组**，同日多份按时间点展示（「08:50 早盘前决策」「09:20 竞价复盘」「12:30 午间复盘」「15:05 收盘复盘」），支持删除（二次确认）；
- 空态/加载/错误态全覆盖；字段映射走 normalize 层（严格对齐 §六契约）。

### F2 后端（FastAPI）
- 异步任务（线程 + 进度回调 + 轮询接口），所有长任务不阻塞；
- 历史接口（按日期分组列表 / 按日期+时间点详情 / 删除单份）；
- 静态托管前端 `frontend/dist`；`GET /api/health`；
- 错误一律中文；上传/参数校验（400/413/404 语义明确）。

### F3 复盘引擎（LangGraph 保留）
- 模式感知：取数节点 + 提示词模板按 4 种时间模式切换（§三），分析师流程复用（并行点评→主持人→分歧→辩论→汇总报告）；
- 自动注入历史上下文：昨日收盘复盘 + 当日更早时间点复盘（§七存储设计）；
- 输出：`final_report`（唯一 Markdown）、`analysts[]`、`debate_history[]`、`overall_tags[]`、`degraded`、`disclaimer`；
- LLM 无 Key/失败 → 规则引擎降级并标注 `degraded=True`；数据缺失 → 明确标注「数据缺失/估算」，绝不静默。

### F4 分析师聊天（功能 B）
- 选择标的（个股代码/板块）→ 与指定分析师（或全员）多轮对话；
- 对话上下文 = skill 人格 + 标的实时/历史数据 + 相关历史复盘结论（复用 chroma 检索）；
- 支持追问、指定多位分析师交叉问答（反方观点由另一位分析师对上一轮回答反驳）；
- 对话持久化 `data/chats/`，可按标的/日期查看历史会话；
- **免责声明程序级强制**：后端每条回复组装时追加「仅供参考，不构成投资建议」，前端固定横幅渲染。

### F5 历史存储（同一天多份）
- `data/reviews/{YYYY-MM-DD}/{HHMMSS}/`（meta.json + report/analysis + 数据快照 snapshot）；
- 列表按日期倒序、同日按时间点倒序；支持按日期+时间点查详情、删除单份；
- 复盘时自动引用历史：昨日收盘复盘、当日更早时间点（自动注入分析上下文）。

### F6 数据层（§四）
- 模式化取数（每个模式只取需要的节点）+ 超时 + 降级链 + 中文错误 + 本地缓存；
- 新增能力：竞价数据、分时/分钟线、龙虎榜、涨停梯队明细、板块资金流（视数据源可用性）。

---

## 三、复盘时间模式定义（核心）

| 模式 | 代码 | 适用时间 | 数据输入 | 输出 |
|---|---|---|---|---|
| 早盘前决策 | `pre_market` | 当日 09:15 前 | 隔夜外盘（纳指、富时A50）、昨夜消息、昨日收盘复盘、前日收盘复盘、指数日线/均线 | 今日大盘预判 + 操作计划 |
| 竞价复盘 | `auction` | 09:15–09:25 | 竞价数据：高开/低开幅度、竞价金额、竞价抢筹/砸盘、热门股竞价异动 | 早盘操作建议（能追/回避） |
| 午间复盘 | `noon` | 11:30–13:00 | 上午分时、板块涨幅/资金、涨停/炸板、上午已有复盘对照 | 上午回顾 + 下午走势判断 + 买卖计划 |
| 收盘复盘 | `close` | 15:00 后（**默认**） | 全天行情：指数/涨跌停/板块/情绪/资金/龙虎榜/资讯 | 当日复盘报告 + 明日计划 |

规则：
1. **时间可用性判定**（后端服务端判定 + 前端提示）：所选时间点不在模式窗口内 → 返回中文提示并建议最近可用模式（如 09:40 选竞价 → 提示「竞价数据已结束，建议切换午间复盘或收盘复盘」）；非交易日上午选 pre_market/auction → 提示非交易日。
2. **历史日期补做**：允许任意日期补做任意模式；数据源无历史（如财新资讯仅当天、外盘仅当天、涨停池仅近 30 交易日、分时深度未知）→ 对应数据块标注「数据缺失（数据源保留期外/仅当日）」并继续流程。
3. **同日多份**：同一日期可多次复盘（不同时间点或同时间点重复），每次独立 `HHMMSS` 目录；前端同日按时间点展示。
4. **盘中时间点**（10:30 / 14:00 等）：v1 不新增模式，自动映射到「最近可用模式」（10:30 → 午间复盘前置说明；14:00 → 收盘复盘前置说明）并提示；是否新增盘中模式见 Q6。

---

## 四、数据源调研结论（2026-08-03 实测/代码审读）

### 4.1 现状可行性
| 数据项 | 主源（现状） | 历史深度 | 结论 |
|---|---|---|---|
| 指数日线/均线 | 通达信（akshare `stock_zh_index_daily_tx`） | 长 | ✅ 可用，`data_cache` 已验证 |
| 涨停/跌停池、连板梯队 | 东财（`stock_zt_pool_em` / `dtgc_em`） | **仅约 30 个交易日** | ✅ 可用但历史受限，超期须降级标注 |
| 板块涨幅/资金 | 同花顺（`stock_board_industry_summary_ths` 实时 / `index_ths` 历史） | 实时+历史（逐行业拉取，慢） | ✅ 可用，历史已有缓存 |
| 个股日线/情绪计算 | Tushare `pro.daily` | 长 | ✅ 可用（token 见风险 R5） |
| 财经资讯 | 财新（仅当天）/ 央视 / 百度经济日历 | 财新仅当天 | ⚠️ 历史补做时财新缺失须标注 |
| 隔夜外盘（纳指） | 新浪 `index_us_stock_sina` | **仅当天** | ⚠️ 同上 |
| 全市场涨跌分布/炸板率 | Tushare 计算 | 长 | ✅ 可用 |

### 4.2 新增强化项（按优先级探索）
| 数据项 | 候选源（优先级排序） | 凭据/限制 | 结论 |
|---|---|---|---|
| **竞价数据**（9:15-9:25 高开幅度/竞价金额/抢筹砸盘/异动榜） | ① 开盘啦（需 Cookie 登录态）② 同花顺竞价页 ③ 东财分时 09:25 竞价柱推算 | **开盘啦需用户提供 Cookie** | 待确认 Q1；不可用则降级为 ③ 并标注 |
| 分时/分钟线（午间模式） | ① 东财 `stock_zh_a_hist_min_em` / `stock_intraday_em` ② 通达信本地数据 ③ Tushare（无分时） | 历史深度待实测（可能仅近数日） | 待冒烟验证；超期降级标注 |
| 龙虎榜 | ① 东财 `stock_lhb_detail_em` 等 ② Tushare `top_list` | 公开 | ✅ 新增，接入收盘模式 |
| 富时A50 | ① 新浪期货/东财外盘接口 ② akshare 外盘期货 | 仅当天 | 待实测；不可用则标注缺失 |
| 北向资金 | **已停发实时数据（2024-08 起仅季度披露持仓）** | — | 从需求中剔除，情绪判断改用其他指标（见风险 R4） |

### 4.3 降级链约定（数据层统一实现）
`主源 → 备用源 → 本地缓存 → 明确标注「数据缺失/估算」`；每个数据块携带 `source` 与 `degraded` 标记；
任何降级必须进入报告/快照的可见标注区，禁止静默给错数据。

### 4.4 所需凭据清单（Q1-Q5）
- 开盘啦 Cookie（竞价数据，可选）；
- Tushare token（建议移入 `.env`，确认现有硬编码值仍有效）；
- 通达信安装路径（可选增强：分时/竞价回放）；
- 代理 `127.0.0.1:7890` 可用性确认（akshare/tushare/git push/uv add 均需要）；
- DeepSeek key 已具备（opencode 网关 `.env`）。

---

## 五、架构草案

```
stock_review_crew/
├── app.py                      # 删除（Streamlit）
├── backend/
│   └── main.py                 # FastAPI：jobs / reviews / chats / 静态托管（复用 synalysis 模式）
├── src/stock_review_crew/
│   ├── config.py               # 改造：LLM + Tushare token 只从 .env 读
│   ├── state.py                # ReviewState + ChatState + 模式字段
│   ├── graph.py                # 重构：模式感知（取数节点按 mode 分发）+ 进度钩子 + 降级
│   ├── prompts.py              # 4 模式提示词模板 + 主持人/复盘助手角色（新增）
│   ├── chat.py                 # 新增：聊天引擎（会话、多分析师交叉、免责声明强制）
│   ├── storage/
│   │   ├── reviews.py          # 新增：data/reviews/{date}/{time} 读写删 + 上下文注入
│   │   └── chats.py            # 新增：data/chats/ 会话与消息持久化
│   ├── tools/
│   │   └── stock_data.py       # 重写：超时/降级链/模式化取数/竞价/分时/龙虎榜
│   ├── knowledge_store.py      # 保留：复盘结论集合；可选扩展聊天检索集合
│   └── skills/__init__.py      # 保留
├── skills/                     # 保留 5 份 skill.json + 新增复盘助手角色（JSON）
├── frontend/                   # 从 synalysis 迁移改造：React + Vite + ECharts
│   ├── src/
│   │   ├── api.js / normalize.js / stages.js / format.js / styles.css
│   │   └── components/（Sidebar / ReviewForm / ProgressOverlay / ReportView /
│   │       Charts / MarkdownView / ChatView / HistoryGroup）
├── data/                       # 新增（gitignore）：reviews/ + chats/
├── data_cache/                 # 保留（gitignore）
├── chroma_db/                  # 保留（gitignore）
├── tests/                      # 重写：pytest 全量（数据/引擎/聊天/存储/后端集成/e2e）
├── scripts/                    # run.ps1 / smoke.ps1 / start_tunnel.ps1（可选）
└── docs/                       # requirements.md（确认版）/ PROGRESS.md / issues.md
```

### 任务流水线（复盘）
```
POST /api/reviews {date, mode, ...} → job_id
→ 取数(模式化, 每源≤30s 超时, 降级链) → 资讯分析 → 趋势派(并行) → 情绪派(并行)
→ 主持人 → 辩论(循环≤3) → 报告(强制免责声明) → 存 data/reviews/{date}/{HHMMSS}/
GET /api/jobs/{id} → {status, stage, pct, message, result|error}
```

### 聊天流水线
```
POST /api/chat/sessions {target_type, target, analysts[]} → session_id
POST /api/chat/sessions/{id}/messages {content} → 回复
上下文 = skill 人格 + 标的实时/历史数据 + chroma 相关复盘结论 + 历史消息；
多分析师交叉：轮询每位分析师对上一轮回答发表同意/反对 → 汇总回复；
每条回复强制追加免责声明。
```

---

## 六、API 契约草案（前端依赖，确认后冻结）

> 约定：比率一律**小数**（0.5=50%）；金额单位元；日期 `YYYY-MM-DD`；时间点 `HHMMSS`；
> `None` = 无数据（前端显示「—」，禁止 0）；前端负责全部格式化（×100/中文）；
> 所有投资相关内容响应携带 `disclaimer` 字段，后端程序级强制。

```
GET  /api/health                          → {status:"ok"}

POST /api/reviews                         Body: {date, mode, max_rounds?} → {job_id}
GET  /api/jobs/{job_id}                   → {job_id, status(queued|running|done|error),
     stage, pct, message, analysts_done, analysts_total, result|null, error|null}
     result = {record_id, meta, report, snapshot}
     meta = {date, mode, mode_label, time, filename, degraded[], disclaimer, ...}

GET  /api/reviews                         → 按日期倒序分组：
     [{date, items:[{record_id, mode, mode_label, time, created_at, summary}]}]
GET  /api/reviews/{date}/{time}           → {meta, report, analyses[], debate_history[], snapshot}
DELETE /api/reviews/{date}/{time}         → 204 / 404（中文）
GET  /api/reviews/context?date=&mode=     → 昨日收盘复盘摘要 + 当日更早时间点复盘摘要
     （供自动注入，返回 {yesterday, earlier_today[]}）

POST /api/chat/sessions                   Body: {target_type:stock|sector, target,
     analysts:["alang",...], title?} → {session_id}
POST /api/chat/sessions/{id}/messages     Body: {content} → {session_id, messages:[...],
     disclaimer}
GET  /api/chat/sessions?target=&date=     → 会话列表（按标的/日期过滤）
GET  /api/chat/sessions/{id}              → {meta, messages[], disclaimer}
DELETE /api/chat/sessions/{id}            → 204 / 404（中文）
```

前端 normalize 层严格对照本契约（参考 synalysis `verify-normalize.mjs` 做法，写断言脚本）。

---

## 七、存储设计

```
data/reviews/{YYYY-MM-DD}/{HHMMSS}/
├── meta.json        # {date, mode, mode_label, time, created_at, max_rounds,
│                    #  degraded[], sources[], disclaimer, summary}
├── report.json      # {final_report, analysts[], debate_history[], overall_tags[],
│                    #  disclaimer, degraded, round_count}
└── snapshot.json    # 模式化数据快照（含每块 source/degraded 标注）

data/chats/{YYYY-MM-DD}/{HHMMSS}/
├── meta.json        # {session_id, target_type, target, analysts[], created_at,
│                    #  title, disclaimer}
└── messages.json    # [{role, analyst?, content, created_at, references[]}]
```

- 同一天多份：按 `HHMMSS` 天然区分，前端显示「HH:MM + 模式标签」；
- 删除单份：仅删对应目录（204），列表刷新；
- 上下文注入：复盘启动前调用 `GET /api/reviews/context`，把「昨日收盘复盘」与
  「当日更早时间点复盘」写入初始 state（沿用现有 `yesterday_report` 字段机制）；
- 旧 `output/` 目录不迁移（仅作历史存档），如需要可写一次性导入脚本（Q10）。

---

## 八、口径假设与数据字典约定（照搬 synalysis 铁律）

1. **比率一律小数存储**：`0.5 = 50%`；上游 API 返回的百分比数值（如 `pct_change=9.98`）在
   进入契约前统一 `/100` 为小数（快照 raw 保留原值并标注单位）；前端 ×100 展示。
2. **None = 无数据**：前端显示「—」，禁止显示 0；非有限数（NaN/Inf）输出 None。
3. **红涨绿跌**：正收益红 `#E03131`、负收益绿 `#0CA678`、零中性灰；警示/免责声明框不参与。
4. **中文展示**：月份「2025年11月」、日期「2025-11-27」、状态「进行中/已完成」、
   来源「东方财富/同花顺/Tushare/通达信/数据缺失」。
5. **情绪指标口径**（沿用现有公式并写死）：昨日涨停今日平均收益/红盘率/连板率/核按钮率；
   炸板率 = 炸板数 ÷ 摸过涨停数；涨跌家数比 = 上涨 ÷ 下跌；涨停口径按板块差异化
   （主板 10%、创业板/科创板 20%），过滤 ST/北证。
6. **竞价指标口径**（数据源确认后冻结）：高开幅度 = (竞价参考价 − 昨收) ÷ 昨收；
   竞价金额 = 9:25 集合竞价成交额；抢筹/砸盘 = 9:20–9:25 委托净流入方向；
   异动榜 = 竞价涨跌幅/金额排名前列。
7. **免责声明**：`DISCLAIMER = "仅供参考，不构成投资建议"`；报告与聊天回复在组装层强制追加，
   响应 JSON 恒含 `disclaimer` 字段；前端报告页/聊天页固定横幅。
8. **降级链**：主源失败→备用源→缓存→标注「数据缺失/估算」；LLM 无 Key→规则引擎
   （`degraded=True` 可见标注）；历史缺失→明确说明，禁止编造。
9. **边界保护**：每源请求超时 ≤30s；LLM 调用超时 120s；任务总时长上限；磁盘写入失败
   不阻塞主流程（记录错误）。

---

## 九、开发流程与交付物（对齐 synalysis §三-§七）

### 9.1 Issue 拆分建议（确认后创建于目标仓库）
| Issue | 模块 | 唯一写权限（草案） | 验收标准 |
|---|---|---|---|
| I1 | 数据层（模式化取数/超时/降级链/竞价/分时/龙虎榜/缓存） | `src/stock_review_crew/tools/**`、`tests/test_data*.py` | 每模式取数节点可离线 mock；降级标注断言 |
| I2 | 复盘引擎（模式感知 graph/prompts/主持人/助手角色） | `src/stock_review_crew/{graph,state,prompts}.py`、`skills/**`、`tests/test_graph*.py` | 4 模式各 1 条无 Key 降级链路测试 |
| I3 | 聊天引擎（会话/交叉问答/免责声明/持久化） | `src/stock_review_crew/chat.py`、`storage/chats.py`、`tests/test_chat*.py` | 单/多分析师 + 免责声明断言 |
| I4 | 历史存储与上下文注入 | `storage/reviews.py`、`tests/test_storage*.py` | 同日多份/删除/上下文注入测试 |
| I5 | 后端 API（jobs/reviews/chats/静态托管） | `backend/**`、`tests/test_backend*.py` | TestClient 全链路 + 中文错误 |
| I6 | React 前端（复盘/进度/报告/聊天/历史分组） | `frontend/**` | `npm run build` + normalize 断言 |
| R1（Wave 2） | 全量业务逻辑审计 | 只读，输出审计报告 | 对照本文档逐字段审计 |

规则：并发 ≤5；每个 agent 明确写权限列表与只读契约；禁止 git；禁止真实数据入库；
模块负责人完成后**保留待命**，登记表写入 `docs/PROGRESS.md`，后续修改 resume 复用。

### 9.2 测试与冒烟
- `python -m pytest` 全绿（含手算抽查用例：每个指标至少一个可手工复算的用例）；
- 前端 `npm run build` 成功 + normalize 契约断言脚本；
- 真实数据冒烟（4 模式各至少 1 次）：
  - 今日（2026-08-03 周一，当前 11:45，**午间窗口内**）：noon 模式实时冒烟；
  - 历史日期补做：pre_market / auction / close 各 1 次（选 2026-07-31 等近期交易日，
    涨停池/分时在保留期内）；
  - 每条记录：日期、模式、数据源与降级标注、输出摘要，写入 PROGRESS.md。

### 9.3 Git 与交付
- 补 `.gitignore`（§1.1）→ 首次提交 → 推送到 `stock_crew.git`（走代理 127.0.0.1:7890）；
- GitHub issues 关联模块与验收标准；
- 交付物：确认版需求文档、代码、全量测试、冒烟记录、README、启动脚本、
  PROGRESS.md（agent 登记表）；可选 Cloudflare Tunnel（参考 synalysis `start_tunnel.ps1`）。

---

## 十、风险清单

| # | 风险 | 影响 | 对策 |
|---|---|---|---|
| R1 | 竞价数据源不可用（无开盘啦 Cookie） | 竞价模式核心数据缺失 | 降级为东财分时竞价柱推算 + 明确标注；或推迟竞价模式至数据可用 |
| R2 | 东财涨停池仅近 30 交易日 | 历史日期复盘降级 | 标注「数据缺失（数据源保留期外）」并继续 |
| R3 | 分时/分钟线历史深度未知 | 午间模式历史补做受限 | 冒烟实测；超期降级标注 |
| R4 | 北向资金实时数据 2024-08 起停发 | 情绪指标缺一 | 剔除北向，改用涨跌家数/昨日涨停表现/炸板率 |
| R5 | Tushare token 硬编码在 config.py（源码泄露风险），`.env` 未含 | 凭据安全 + 换机失效 | 移入 `.env`（Q2 确认），`.env` 不入 git |
| R6 | 网络依赖代理与公网 API 稳定性 | 取数失败/超时 | 超时+降级链+缓存；冒烟前置网络验证 |
| R7 | 旧 `output/` 数据不兼容新存储 | 历史上下文丢失 | 知识库 chroma 仍可检索；可选一次性导入脚本（Q10） |
| R8 | 首次 git 提交把真实数据/凭据入库 | 隐私泄露 | 先补 `.gitignore` 再 add；提交前审查清单 |
| R9 | 聊天多分析师并发导致 LLM 调用时长不可控 | 用户体验差 | 串行交叉问答（每轮 ≤2 位回应）+ 120s 超时 + 进度提示 |
| R10 | 前端字段映射与后端契约漂移 | 整页失效 | normalize 层 + 契约断言脚本（延续 synalysis 教训） |

---

## 十一、待确认问题（Q1-Q10，建议默认值）

| # | 问题 | 建议默认 | 需用户确认 |
|---|---|---|---|
| Q1 | 开盘啦账号 Cookie / 登录态是否提供？（竞价数据主源） | 先不提供 → 竞价模式降级实现，数据源留接口 | ☐ |
| Q2 | Tushare token：是否允许把 config.py 硬编码值移入 `.env`？（不打印值） | 允许，移入 .env 并验证可用 | ☐ |
| Q3 | 通达信安装路径是否提供？（分时/竞价回放增强） | 不提供 → 仅用 akshare 在线源 | ☐ |
| Q4 | 代理 `127.0.0.1:7890` 是否可用？（akshare/tushare/git push/uv add 均需要） | 默认可用，冒烟前置验证 | ☐ |
| Q5 | 数据源优先级与降级策略是否按 §4.3 执行？ | 按草案执行 | ☐ |
| Q6 | 是否需要盘中模式（上午/下午盘中）？ | v1 不做，时间点自动映射最近可用模式并提示 | ☐ |
| Q7 | 聊天范围：单分析师/多分析师交叉/历史对话检索 | 三档全做（检索复用 chroma） | ☐ |
| Q8 | 聊天入口位置（侧栏唯一主按钮约束下） | 内容区首页卡片入口 + 侧栏「对话记录」分组 | ☐ |
| Q9 | 前端首页布局：复盘表单 + 聊天卡片 + 历史分组，是否认可 | 按草案 | ☐ |
| Q10 | 旧 `output/` 历史是否需一次性导入脚本？ | 不迁移，仅存档；需要时另开小任务 | ☐ |

> 确认方式：对上述清单逐条回复（如「Q1 提供 Cookie」「Q2 同意」），或整体「按建议默认执行，
> 除 … 外」。确认后本文升级为 `docs/requirements.md` 并开始 §9 流程。
