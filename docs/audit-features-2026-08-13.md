# Stock Review Crew 全量功能与技术债审计报告

- 审计日期：2026-08-13（基于当前工作区代码）
- 审计范围：backend/**、src/stock_review_crew/**（含 accounts/、tools/、trades/、storage/）、frontend/src/**、skills/**、tests/**（文件名与意图）、docs/requirements.md
- 审计方式：只读代码审计，未运行服务/测试，未修改任何代码
- 说明：本报告为静态审计结论，部分「待确认」项需运行回归（scripts/regression.py）或人工冒烟验证

---

## 一、功能总清单（维度 A：用户可见功能）

> 状态图例：✅ 正常（代码与入口齐备）｜⚠️ 半成品/有 bug/口径变化｜🗑️ 疑似死代码/无人使用
> 「数据源」列按代码主链路概括，降级链细节见 requirements.md §四。

### P0 复盘引擎

| # | 功能名称 | 入口（前端+路由） | 后端实现 | 数据源 | 状态 |
|---|---|---|---|---|---|
| 1 | 6 模式复盘（pre_market/auction/intraday_am/noon/intraday_pm/close） | 侧栏「补做复盘」+ ReviewTab 自动触发 → `POST /api/reviews` | `graph.py` fetch_data_node/validate_data_node + `tools/stock_data.py` fetch_mode_data | 指数/涨停/板块/情绪/竞价/分时/龙虎榜/资讯多源降级链 | ✅ |
| 2 | 复盘任务进度轮询（取数→资讯→分析师→主持人→辩论→报告→完成） | ProgressView + App.beginPolling → `GET /api/jobs/{id}` | `backend/jobs.py` `_run_review_job` + graph `_emit`（ContextVar 并发） | — | ✅ |
| 3 | 15:00 后自动生成收盘复盘（R11 R2） | ReviewTab.runAutoCheck → `GET /api/reviews/status` | `routes_review.py` `_status_payload` + `_resolve_latest_date` | — | ✅ |
| 4 | 历史复盘列表/详情/删除（R11 R4：每日一份） | Sidebar/ReviewTab/ReportView → `GET/DELETE /api/reviews/{date}` | `storage/reviews.py`（新结构单份 + 旧结构兼容 + 覆盖前归档） | data/reviews | ✅（⚠️ 与 requirements.md §一.5/§五 旧契约冲突，见二-10） |
| 5 | 复盘报告展示（Markdown + 分析师折叠卡 + 辩论记录 + ECharts 快照） | ReportView.jsx + Charts.jsx + MarkdownView.jsx | `storage/reviews.py` get_review | report.json/snapshot.json | ✅ |
| 6 | LLM 无 Key/失败降级 | 同 #1 | graph：分析师/主持人/辩论/资讯走规则引擎（`_rule_analyst/_rule_host/_RULE_DEBATE/_rule_news_analysis`）；**报告节点改为直接终止**（`report_node` 抛 DataValidationError） | — | ⚠️ 降级口径分裂：部分环节降级、报告环节终止，产品语义不统一（graph.py:1671-1743） |
| 7 | 历史上下文注入（昨日收盘复盘全文 + 当日更早复盘摘要） | 复盘自动注入 | `jobs.py` `_yesterday_report_text`/`_context_item_text` + `reviews.context` | data/reviews | ✅ |
| 8 | 复盘质量评分 + Langfuse 追踪（trace_review/score_review） | 复盘完成后附带 quality 字段 | `quality.py`（1059 行，rubric + 数字核验）；无凭据自动 no-op | Langfuse | ✅ |
| 9 | 分析师模拟账户快照进 meta（R12） | 报告 meta.accounts | `graph.py` `_compact_account` + jobs meta | data/accounts | ✅ |

### P0 模拟下单（accounts/）

| # | 功能名称 | 入口 | 后端实现 | 数据源 | 状态 |
|---|---|---|---|---|---|
| 10 | 5 位分析师模拟账户（10 万初始，T+1/涨跌停/费用/整百撮合） | AccountsPanel.jsx → `GET /api/accounts`、`GET /api/accounts/{skill_id}` | `backend/accounts_routes.py` + `accounts/engine.py`（place_order/snapshot/trade_stats/closed_trades） | data/accounts/*.json | ✅（2026-08-13 刚修复历史价估值口径） |
| 11 | 分析师文本自动下单解析（正则提取推荐行 → 计划） | 复盘流水线内自动执行 | `graph.py:857-968` 三组正则 + `accounts/plan.py` registry | — | ⚠️ 三段几乎相同正则重复；与 prompts.py 冻结格式强耦合，任一格式漂移即漏单（graph.py:871-966） |
| 12 | 交易计划落盘 plan.json（唯一真源） | 复盘 done 前自动 finalize | `accounts/plan.py`（线程安全 registry + 整文件覆盖）+ `jobs.py:434-438` | data/reviews/{date}/plan.json | ✅ |
| 13 | 账户全量重放（确定性 + 合规断言） | CLI：`python -m stock_review_crew.accounts.replay` | `accounts/replay.py`（867 行，run_replay + AssertionChecker + ComplianceReporter） | plan.json + quotes_cache | ⚠️ 无前端/API 入口，仅脚本与测试使用；与 #14 存在两套撮合入口 |
| 14 | 增量撮合 settle_plans（逐日交错流程） | 无 API/前端入口 | `accounts/replay.py:714-844` | quotes_cache | ⚠️ 调用方待确认（猜测为批跑脚本）；与 #13 重复实现撮合循环 |
| 15 | 行情缓存 data/quotes_cache/{date}.json | 重放/撮合自动读写 | `accounts/quotes.py`（历史日期拒绝实时价兜底，2026-08-13 修复） | 缓存 + 腾讯兜底 | ✅ |

### P1 数据展示（tools/ + routes_data.py + 前端）

| # | 功能名称 | 入口 | 后端实现 | 数据源 | 状态 |
|---|---|---|---|---|---|
| 16 | 实时行情快照（指数/涨跌停/板块资金/快讯） | DataTab → `GET /api/data/realtime` | `tools/realtime.py` fetch_realtime_snapshot + enrich_indices_updown（R9 涨跌家数） | 东财 push2/腾讯/通达信协议 | ✅ |
| 17 | 个股实时行情（五档） | DataTab 查询框 → `GET /api/data/quote` | `tools/realtime.py` fetch_stock_quote（东财→腾讯→通达信协议） | 同上 | ✅ |
| 18 | 个股详情（实时/日K/分时/1m/5m/均线） | StockDetail.jsx → `GET /api/data/stock/{code}` | `tools/stock_data.py` fetch_stock_detail | 通达信协议/腾讯/东财 | ✅ |
| 19 | 股票名称/代码搜索 | DataTab/ChatTab/聊天标识别 → `GET /api/data/search` | `tools/realtime.py` search_stocks（东财 suggest→腾讯 smartbox） | 东财/腾讯 | ✅ |
| 20 | 批量代码→名称（M5，2026-08-12） | 聊天气泡/报告代码渲染 → `POST /api/data/stock-names` | `routes_data.py` resolve_stock_names（5s 缓存 + 腾讯批量 + 单代码兜底） | 腾讯 qt.gtimg.cn | ✅ |
| 21 | 市场天气/情绪温度（R10 F3） | TodayDataCards WeatherCard → `GET /api/data/market-weather` | `tools/market_extra.py` fetch_market_weather/compute_market_weather | zt_pool/breadth/sentiment 三块并行 | ✅ |
| 22 | 纳斯达克（R10 F6） | NasdaqCard → `GET /api/data/us-nasdaq` | `market_extra.py` fetch_us_nasdaq | 新浪/东财/腾讯 | ✅ |
| 23 | 板块热度榜（R10 F5） | SectorHeatCard → `GET /api/data/sector-heat` | `market_extra.py` fetch_sector_heat | 东财板块 | ✅ |
| 24 | 额外指数（平均股价/微盘/高股息，R10 F2） | ExtraIndicesStrip → `GET /api/data/extra-indices` | `market_extra.py` fetch_extra_indices | 东财/通达信协议 | ✅ |
| 25 | 风格坐标（大盘vs小盘/价值vs成长，R10 F4） | StyleCard → `GET /api/data/market-style` | `market_extra.py` fetch_style_quad | 东财 | ✅ |
| 26 | 统一情绪指标（R12） | 首页/复盘共用 → `GET /api/data/market-sentiment` | `market_extra.py` fetch_market_sentiment | 同上三块 | ✅ |
| 27 | 观察列表（自选，R10 F9） | WatchlistPanel → `GET/POST /api/watchlist`、`DELETE/PUT` | `storage/watchlist.py`（原子写 + 锁） | data/watchlist.json | ✅ |
| 28 | 设置页（主题/涨跌配色/字号/图表周期） | SettingsView.jsx → 纯前端 | `settings.js`（localStorage + useSyncExternalStore） | localStorage | ✅ |
| 29 | 离线 mock 模式（DEV/`?offline=1`） | api.js 全量降级 | `mock.js`（872 行）+ api.js mock 分支 | — | ⚠️ 双轨维护成本高；mock 分析师名与真实不一致（见二-11）；`getChatSession`/`streamChatMessage` 对 mock 会话**先查 mock 再查后端**，同日同秒的会话 id 有被 mock 顶替风险（api.js:862、1010-1012，待确认） |

### P1 交割单分析（trades/）

| # | 功能名称 | 入口 | 后端实现 | 数据源 | 状态 |
|---|---|---|---|---|---|
| 30 | 交割单上传解析（xlsx/xls/csv/txt） | TradesTab → `POST /api/trades/upload` | `trades/parser.py`（券商无关列名别名表 + 表头探测 + 合同号脱敏） | 同花顺/涨乐导出 | ✅ |
| 31 | 交割单指标（TWR/胜率/盈亏比/翻倍腰斩/持仓周期/风格画像等） | 上传后自动计算 + `GET /api/trades/{trade_id}` | `trades/metrics.py` compute_metrics（固定 JSON Schema MetricsResult） | records.json + 行情兜底 | ✅（⚠️ 单函数 800+ 行，见二-4） |
| 32 | AI 行为画像报告（5 分析师并行+报告，LLM 降级规则引擎） | TradesTab「生成分析报告」→ `POST /api/trades/{id}/analyze` | `trades/analyst.py` analyze | 指标 + LLM | ✅（R12 后 host/debate 已去除，相关函数成死代码，见二-5） |
| 33 | 历史交割单列表/回看 | TradesTab 历史列表 → `GET /api/trades` | `trades_routes.py` list_trades | data/trades | ✅ |

### P2 分析师聊天（chat.py + routes_chat.py + ChatView）

| # | 功能名称 | 入口 | 后端实现 | 数据源 | 状态 |
|---|---|---|---|---|---|
| 34 | 会话创建/列表/详情/删除 | ChatTab/ChatView → `POST/GET/DELETE /api/chat/sessions[...]` | `chat.py` ChatEngine + `storage/chats.py` | data/chats | ✅ |
| 35 | 单/多分析师问答（R8 六步：parse→need→fetch→analyze→summarize→组装） | ChatView 输入框 → `POST .../messages` 或 `/messages/stream` | `chat.py` `_run_pipeline`（每位分析师数据清单 + 白名单取数 + 逐位回答 + 汇总） | tools fetch_* 白名单 + chroma | ✅ |
| 36 | SSE 流式聊天（stage/analyst_*/summary_*/done/error + 15s 保活 + 30min 兜底） | ChatView 流式渲染 | `routes_chat.py` stream_chat_message | — | ✅ |
| 37 | 历史对话持久化 + 在途轮次恢复（R11 R10） | ChatView 挂载恢复 + 轮询接续 | chat.py `_compute_pending_round` + api.js sessionStorage 标记 | data/chats | ✅ |
| 38 | 历史对话检索（chroma chat_history 集合） | 上下文自动注入 | `chat.py` save/search_chat_history | chroma_db | ✅ |
| 39 | 相关复盘结论检索 | 上下文自动注入 | `knowledge_store.py` search（review_history 集合） | chroma_db | ✅ |
| 40 | 联网搜索简报（R11 R9） | 问答流水线自动 | `tools/search.py` + chat `_search_and_brief` | 博查（默认关）/新浪 7x24 快讯 | ⚠️ 博查默认关闭，主链路实际只有快讯过滤 |
| 41 | 消息复制/纯文本/代码转名称展示（M5） | ChatView/ReportView | `format.js` withStockNames | 批量名称接口 | ✅ |

### 系统层

| # | 功能名称 | 入口 | 后端实现 | 状态 |
|---|---|---|---|---|
| 42 | 健康检查（含 Langfuse/资讯开关） | `GET /api/health` | `main.py` | ✅ |
| 43 | 静态托管 frontend/dist | `/` | `main.py`（dist 存在时挂载） | ✅ |
| 44 | 旧 output/ 历史导入脚本 | CLI：scripts/import_output_history.py | storage/reviews 兼容 | ✅（脚本，未在本次范围深读） |
| 45 | 资讯总开关 NEWS_ENABLED（默认关闭） | 复盘/聊天/快讯三处联动 | `config.news_enabled()` + graph/chat/_parallel_blocks | ✅ |

---

## 二、屎山与技术债清单（维度 B，按文件，每条一行）

> 格式：`文件:行号` — 问题 — 建议

### 数据层（最重灾区）

1. `src/stock_review_crew/tools/_base.py:26` — **语法损坏**（`REQUIRED_INDEXES,` 无头缩进，IndentationError），整文件是「拆分 stock_data.py」失败残留的半成品；同目录另有 10 个空壳 stub（`_auction/_zt_pool/_breadth/_index_daily/_lhb/_macro_news/_minute/_sectors/_sentiment/_stock_info.py`，均只有 docstring+import 无任何函数）。除 `_base`↔stub 互相 import 外，全仓库无任何模块引用它们（stock_data.py 不 import `_base`）→ **死代码簇**。建议：整组删除，杜绝误导。
2. `src/stock_review_crew/tools/stock_data.py:2691-2694` — 注释自述「2026-08-13 恢复：以下为最近几天的未提交改动（子代理拆分 stock_data.py 失败后，从 __pycache__ 的 3.13 字节码逐条还原）」——**从字节码还原的代码无版本史佐证**，正确性/可维护性风险最高；该文件 3083 行（>800 行红线的 3.8 倍），承载 6 模式取数+全降级链+缓存+LangChain 工具包装+个股详情聚合。建议：按 `_*.py` 声明的 10 个数据块真实拆分，或删 stub 后至少把 `get_*` 工具包装与纯函数分离。
3. `src/stock_review_crew/tools/market_extra.py:466-504 与 512-535` — `fetch_market_weather` 与 `fetch_market_sentiment` 的「三块并行取数 + 缺块补 degraded」代码**逐字重复**。建议：提取公共 `_fetch_weather_blocks(date, join_timeout)`。
4. `src/stock_review_crew/tools/realtime.py:279` vs `backend/routes_data.py:115-122` — `_tx_symbol` 与 `_stock_name_tx_symbol` 同一逻辑（6/9/5→sh、4/8/92→bj、其余→sz）两份实现。建议：下沉到公共模块（如 tools/_base 或新 common）。
5. `src/stock_review_crew/tools/stock_data.py:247` — `KAIPANLA_API_URL` 注释「端点如与官网调整不符，可在测试中注入」；竞价链路依赖外部站点页面结构（同花顺/开盘啦），属外部脆弱点，已在降级链内但需留意。
6. `src/stock_review_crew/tools/_stock_info.py:3-4` — 唯一一个「有解释为什么保留在门面」的 stub，说明拆分思路曾存在但中途废弃，其余 stub 连解释都没有。

### 复盘引擎与后端

7. `src/stock_review_crew/graph.py:871-966` — **自动下单正则三段重复**：结构化（股数式）+ 成数式（后顾断言版）+ 结构化成数式，三组循环结构几乎相同，仅正则/来源标记不同；解析失败全部 `except BaseException: continue` 静默。建议：抽 `parse_plan_lines(text, held_codes, date, skill)` 纯函数 + 单测，graph 只调一次。
8. `src/stock_review_crew/graph.py:1223-1224` — `_rule_analyst` 仍用 `skill.get("prompt")[:120]` 填充人设，**与 PLAYBOOK §8[2026-08-06]「改展示 skill.role，禁止输出 prompt 原文」的修复记录矛盾**——规则引擎降级时角色提示词原文前 120 字仍会进入分析文本（提示词泄漏，虽仅降级路径）。建议：改为 `skill.get("role")`。
9. `src/stock_review_crew/graph.py:1671-1743` — `report_node` 在 LLM 失败时 `raise DataValidationError` 终止整场复盘，而分析师/主持人/辩论/资讯仍走规则引擎降级——**同一系统两套失败语义**（部分降级 vs 整体终止），README/需求口径需明确。建议：统一为「LLM 全不可用→终止，局部失败→降级」并在文档写明。
10. `backend/main.py:166-227` — 「测试兼容 re-export」块把 jobs/snapshot/routes_data/routes_review/routes_chat 的约 60 个**私有名**（`_run_review_job`、`_to_frontend_snapshot`、`_market_extra_call`…）重新导出到 backend.main；11 个测试文件直接 `import backend.main as bm` 并 monkeypatch 私有函数；`jobs.py:45-52` 的 `_host()` 再经 `sys.modules["backend.main"]` 实时读宿主属性——**测试与实现细节深度耦合 + 三层间接**。建议：测试改 import 目标模块 + 显式依赖注入，删除 re-export 块与 `_host`。
11. `backend/jobs.py:341-459` — `_run_review_job` 单函数 120 行承载「上下文注入→引擎→质量评分→落盘→计划 finalize→job 状态」6 类职责；且质量评分/落盘失败只 append degraded。建议：按阶段拆小函数（如 `_quality_pass`、`_persist_review`）。
12. `backend/main.py:92-115` — `_flatten_included_routers` 为兼容 fastapi>=0.140 懒加载 include_router 写的运行时 hack（改 `app.router.routes`）；版本兼容补丁进业务代码，应在依赖层面锁定版本或去掉。建议：确认 fastapi 版本后移除。
13. `docs/requirements.md`（全文）— **「唯一契约」已与代码脱节**：§一.5 同日多份 `data/reviews/{date}/{HHMMSS}/` vs 实际 R11 R4 每日一份+归档；§五 `GET /api/reviews/{date}/{time}` vs 实际 `/{date}`；另有 `requirements-v4`、`r10-r15` 7 份增量文档并存，字段真源分散（plan.json 在 r14 §四.4.1、quotes 缓存在 r14 §四.4.2）。建议：合并为单一真源或建立 supersede 链并在文件头标注生效范围。
14. `src/stock_review_crew/state.py:5-39` — ReviewState 只有 39 行 TypedDict，但实际运行状态被不断加字段（market_weather/analyst_accounts 等），**未定义键靠运行时拼装**，LangGraph 类型检查形同虚设。建议：补齐 schema 与 graph 返回键对齐。

### 交割单

15. `src/stock_review_crew/trades/metrics.py:823-1200` — `compute_metrics` 单一函数约 800+ 行、30+ 个局部状态变量 + 3 个嵌套闭包（`holding_cost_now`/`finalize_twr_day`/`update_max_pos`，L951-1013），TWR 逐日模拟、FIFO 配对、月结、翻倍腰斩全在一处；文件 1647 行。建议：拆 `TwrSimulator`/`StockLedger` 类，闭包提为方法。
16. `src/stock_review_crew/trades/analyst.py:848-903, 1024` — `_run_host`/`_run_debate_one`/`_run_debate_round` 已不被 `analyze` 调用（R12「host/debate 环节已去除」），但保留完整实现 + 规则引擎 `rule_host`/`rule_debate_response`/`build_host_prompt`/`build_debate_prompt` → **死路径**；且该文件与 graph/prompts.py 维护**两套并行分析师 prompt 体系**（交割单一套、复盘一套）。建议：删死代码，或明确「交割单分析」与「复盘」共用人格层的边界。
17. `backend/trades_routes.py:264-293` — `POST /analyze` 为同步返回 `{report, disclaimer}`，但前端 TradesTab（frontend/src/components/TradesTab.jsx:180-212）保留「异步 job 轮询」分支「仅防回归」；`round_count`/`debate_history` 已恒空。建议：删前端异步分支与后端空字段。

### 聊天

18. `src/stock_review_crew/chat.py:83-123` — `_call_llm`/`_stream_llm` **未走 `llm_runtime.invoke_llm`**（无 150s 硬超时 + 重试 + Langfuse 回调），与 graph/analyst 的 LLM 适配层分裂——「同一逻辑两套实现」实证（graph.py:29-37 与 trades/analyst.py:37 已统一到 llm_runtime，唯独 chat 没迁）。建议：chat 迁移到 llm_runtime。
19. `src/stock_review_crew/chat.py:216-244` — FETCH_DISPATCH 同时保留 `minute_data`/`minute_kline`、`market_breadth`/`breadth`、`news_headlines`/`news` 双键（靠 `_ALIAS_TO_CANONICAL` 归一），白名单 30 键与 tools 实际函数有重叠错位。建议：只留规范键，删除别名层。
20. `src/stock_review_crew/chat.py:30` — `CHROMA_COLLECTION = "chat_history"` 与 `knowledge_store.py:41` 的 `review_history` 两套 chroma 集合、两套客户端初始化（chat.py:599-634 复制了 knowledge_store 的客户端模式）——客户端单例逻辑重复。建议：抽公共 chroma 客户端。

### 前端

21. `frontend/src/api.js:378-388` — `getReviewDetail(date, time)` 调用的 `/api/reviews/{date}/{time}` 路由**已被后端移除**（routes_review 只有 `/{date}`），全仓库仅注释引用（App.jsx:358）→ **死导出**。建议：删除。
22. `frontend/src/api.js:1036-1050` — MOCK_STEPS 分析师名（老周/小林/阿凯/陈姐/大熊）与真实 5 分析师（阿狼/爱在冰川/拔小弦/炒股养家/铁锤狂砸盘，mock.js:10-18、skills/）**不一致**，离线演示与真实系统观感断裂。建议：对齐 ANALYSTS。
23. `frontend/src/mock.js`（872 行）— 离线 mock 双轨长期与真实后端并行维护（含 ACCT_DAYS 回测资产曲线模拟）。建议：按「仅 DEV/CI 使用」边界收敛，或改为 JSON fixture。
24. `frontend/src/components/TradesTab.jsx:84-85` — `report`/`analyzeError` 两个 useState **缩进错乱**（缩进为 0，行内嵌于组件顶层下），可读性差；`handleAnalyze` 内含死分支（见 17）。
25. `frontend/src/components/ChatTab.jsx:140-145` — `onSelectSession`/`onPickSession` 缩进错乱（相对 JSX 外层 0 缩进）。建议：统一格式化（Prettier）。
26. `frontend/src/App.jsx:424-436` — `view === "chat"` 渲染分支**不可达**（handleOpenChat/handleSelectChatSession 均 `setView("home")`），保留仅为兼容。建议：删除或加注释。
27. `frontend/src/normalize.js:200` — 注释「GET /api/reviews/{date}/{time}」过时（R4 后为 `/{date}`）；`normalizeWatchlist`（L432-442）兼容 `{items}` 与裸数组，但后端只返数组 → 冗余分支。
28. `frontend/src/styles.css`（2638 行）— 单文件样式；ChatView/ReportView 各自复制 `copyText` 降级实现（ChatView.jsx:68-91、ReportView.jsx:9-32）→ 前端侧重复代码。建议：抽 `clipboard.js`。

### 通用/卫生

29. `src/stock_review_crew/tools/_base.py` 等 11 个文件整体 — 见第 1 条，**最高优先级清理对象**。
30. 仓库根目录 — 80 个 `pytest-cache-files-*` 残留目录 + `trades_verify_*`、`work/`、`.tmp/` 等生成物未清理（本轮只读未验证 .gitignore）。建议：补 .gitignore + 清理脚本。
31. `src/stock_review_crew/accounts/engine.py:27-37` vs `accounts/replay.py:139-148` — `_limit_pct` 两份实现（科创板/创业板 ±20%、主板 ±10%），未来口径变更需同步两处。建议：replay 复用 engine。
32. `backend/jobs.py:63-71` 与 `frontend/src/modes.js:44-51` — 模式窗口（MODE_WINDOWS vs WINDOW）与 `_nearest_mode` vs `currentMode`、`_is_trading_day` vs `isTradingDay` **双份实现**，仅靠注释「严格一致」维持。建议：由后端接口下发或抽共享常量文档。
33. `backend/snapshot.py:28-34` 与 `graph.py:295-308` — 引擎快照键别名匹配（`_find_block` 5-8 个别名元组）两端各一套，新增数据块需同步 3 处（graph._MODE_BLOCKS/_BLOCK_ALIASES、snapshot._find_block、前端 normalize）。建议：收敛为统一契约键。

---

## 三、模块耦合与循环 import 风险（维度 C）

### import 关系图（模块级静态 import，惰性 import 标注）

```
backend/main.py ──> accounts_routes ──> accounts.engine / accounts.storage
              ├──> trades_routes ──> trades{parser,metrics,analyst}
              ├──> routes_review ──> backend.jobs ──> graph, config, storage.reviews, prompts
              ├──> routes_chat ──> chat ──> storage.chats（tools 惰性）
              ├──> routes_data ──> backend.jobs（_market_extra_call/_host）
              └──> snapshot（叶子，无内部依赖）

graph.py ──> prompts ──> skills
         ──> llm_runtime（叶子）
         ──> state（叶子）
         ──> [惰性] accounts.{storage,engine,plan} / tools.stock_data / tools.market_extra / llm_sanitize / knowledge_store

tools.market_extra ──> tools.stock_data（模块级，L34-35）──> config, knowledge_store, tdx_local
tools.realtime ──>（tdx_remote 惰性）
tools.stock_data ──>（tdx_remote/ths_crawler/browser_crawler 惰性）
```

### 结论

1. **无循环 import**：关键环 `market_extra → stock_data` 单向；stock_data 对 market_extra 是函数内惰性 import（graph.py:519、jobs.py:481），模块加载期无环。chat.py 不 import graph；jobs 不 import backend.main（靠 sys.modules 读宿主，规避环）。✅
2. **过度耦合点**：
   - `backend/jobs.py` 同时依赖 graph/quality/storage.reviews/accounts.plan/prompts/market_extra（惰性）——**编排层职责过载**（见二-11）。
   - `graph.py` 的 `_llm_analyst` 直接 import `accounts.engine/storage` 并把账户撮合（`plan_store.add`）嵌入 LLM 工具循环（graph.py:757-768、857-968）——**复盘引擎与模拟下单耦合在函数体深处**，测试/替换引擎困难。
   - `trades/analyst.py` 与 `graph.py` 各自独立组装分析师流程（并行线程池 + 规则引擎 + prompt 构建），**两套「多分析师编排」实现**，未来人格/流程改动需双改。
   - 前端 `App.jsx` 单组件持有全部全局状态（job/report/history/chatSessions/chatIntent…30+ useState）并向下传 10+ props——状态集中、无 Context/Store 抽象，新增视图成本高。
3. **循环风险评级**：低（当前无环），但惰性 import 全库约 25+ 处（`importlib.import_module`/函数内 import），一旦有人把 `market_extra` 提到 stock_data 模块级即构成环——建议把 market_extra→stock_data 改为接口注入。

---

## 四、安全/健壮性（维度 D）

### 结论（每项：证据 → 评估）

1. **硬编码凭据：无** ✅。`config.py` 只读 `.env`（TUSHARE_TOKEN/KAIPANLA_COOKIE/TDX_PATH/BOCHA/DEEPSEEK/LANGFUSE，L49-66、145-154），且 `load_dotenv(override=True)` 以 .env 为唯一真源（L43）。`tdx_remote.py:28-51` 硬编码 22 台主站 IP——是数据源地址而非凭据，可接受但建议挪配置。
2. **异常吞噬：系统性、多为有意设计** ⚠️。全库 `# noqa: BLE001` + `except BaseException: pass` 大量存在（graph/chat/tools 合计 100+ 处），多数有「绝不抛异常/降级标注」注释（数据层、引擎层），符合需求 §六.9「磁盘写入失败不阻塞主流程」。但存在**静默吞异常点**：
   - `graph.py:901-968` 正则下单解析 `except BaseException: continue`——漏单不产生任何日志/降级标注（工具日志仅 tool_log 落盘），用户不可见。
   - `chat.py:676-677` `save_chat_history` 失败静默 False；`knowledge_store.py:78-79/99-100` save 失败 pass。
   - `routes_data.py:50` `enrich_indices_updown` 失败仅 pass（有注释，可接受）。
3. **无超时/弱超时网络调用** ⚠️（重点）：
   - `chat.py:83-123` `_call_llm`/`_stream_llm` 无硬超时包装——`llm_runtime.py:19` 注释自述「httpx timeout 对流式/挂起连接不生效」，chat 恰恰没迁到带 150s 硬超时的 `invoke_llm`；SSE 路由有 30min 兜底（routes_chat.py:181），但**同步 `POST /messages` 无兜底**，最坏挂到 httpx 120s。
   - `stock_data.py:168-189` `_call_with_timeout` 用 daemon 线程 + `join(timeout)`，**超时后线程不回收继续执行**——高并发复盘/聊天下每源每请求一个幽灵线程，存在线程泄漏面（全库该模式约 15+ 处：realtime/market_extra/tdx_remote 同款）。
   - `stock_data.py:252-276` `_http_get_json` 东财子域轮换 3 次无总预算（单次 15s×3=45s），超出「每源 ≤30s」契约（好在 `_parallel_blocks` 45s 兜底）。
   - `tdx_remote.py:116-143` 22 台主站轮换最坏 22×(5s connect+3s 超时)≈176s，远超 30s 契约（依赖 `_parallel_blocks` 45s 兜底）——**契约与实现不一致**，待确认实际触发频率。
   - `knowledge_store.py:43` ONNX 嵌入模型首次加载可能联网下载模型，无超时控制（chroma 内部行为）——待确认。
4. **目录穿越防护：好** ✅。`storage/chats.py:43-52`、`storage/reviews.py:114-124`、`trades_routes.py:119-131` 均有正则校验 + resolve 越界检查。
5. **前端网络健壮性：好** ✅。api.js 统一 30s fetch 超时（聊天 240s）+ AbortController + 中文错误；SSE 注释行保活 15s。
6. **并发安全** ⚠️：`JOBS`/`watchlist`/`plan registry` 有锁；但 `chat_storage.append_message` 读-改-写无锁（routes SSE worker 与轮询可并发读），同秒并发追加可能丢消息——待确认实际并发度。
7. **数据一致性**：`accounts_routes._summary` 总资产按最近结算日快照估值（2026-08-13 修复，accounts_routes.py:97-135），与收益率曲线同源，口径已收敛 ✅。

---

## 五、核心模块健康度评分

| 模块 | 分数 | 一句话理由 |
|---|---|---|
| 复盘引擎（graph.py/prompts.py/skills/） | **3/5** | 流水线/降级链/免责声明强制质量高，但 1830 行单文件 + 正则下单强耦合 + 「局部降级 vs 报告终止」语义分裂 + PLAYBOOK 修复记录与代码矛盾（_rule_analyst 提示词泄漏）。 |
| 模拟下单（accounts/） | **4/5** | engine 撮合口径清晰、手算基准冻结（test_r12_acceptance）、确定性重放+合规断言是亮点；扣分在 plan 正则脆弱、replay 867 行双撮合入口、私有 _settle_day 被外部调用。 |
| 数据层（tools/） | **2/5** | 功能最全但健康度最低：3083 行 stock_data + 语法损坏 _base.py + 10 空壳 stub + market_extra/realtime 重复实现，是「拆分失败」直接现场，重构风险最高。 |
| 交割单（trades/） | **3/5** | parser 券商无关列名映射设计好；compute_metrics 单函数 800+ 行难维护；analyst.py 残留 host/debate 死代码与并行 prompt 体系。 |
| 聊天（chat.py） | **3/5** | 六步流水线/SSE/在途恢复设计完整、兜底健壮；但 1451 行 + LLM 适配层未并入 llm_runtime（无硬超时）+ chroma 客户端复制。 |
| 前端（frontend/src） | **4/5** | 组件化、normalize 契约层、错误态与离线 mock 完备；扣分在 api.js 1125 行 + mock 双轨 + 死导出/缩进错乱/不可达分支等小债。 |

---

## 六、重构优先级建议（按投入产出比，≤10 条）

| 优先级 | 建议 | 预计改动范围 | 理由 |
|---|---|---|---|
| P0-1 | **删除 `tools/_base.py` + 10 个空壳 `_*.py` 死代码簇** | 仅删 11 个文件，零行为影响 | 消除语法损坏文件与「假拆分」误导；当前无任何代码引用，纯收益（先跑一次回归确认无引用）。 |
| P0-2 | **修复 `graph.py:1223` `_rule_analyst` 提示词泄漏**（改 `skill.role`） | graph.py 1 处 + 单测 | 与 PLAYBOOK 修复记录矛盾，降级路径会把人格提示词原文注入分析，属数据/提示词外泄。 |
| P1-3 | **合并需求文档真源**（requirements.md + v4 + r10~r15 → 单文件或 supersede 链） | 仅 docs/ | 「唯一契约」已与代码脱节（R11 R4 每日一份、/{date} 路由、plan.json 字段），是后续一切重构的地基。 |
| P1-4 | **chat.py 迁到 `llm_runtime.invoke_llm`**（硬超时+重试+回调） | chat.py 约 3 处调用点 + 测试 | 消除 LLM 适配层分裂与同步消息无硬超时风险，聊天是用户高频路径。 |
| P1-5 | **抽公共取数块函数消除 market_extra 重复**（`fetch_market_weather`/`fetch_market_sentiment` 三块并行代码） | market_extra.py 重构 + 现有测试回归 | 1218 行文件的确定性去重，行为不变。 |
| P2-6 | **拆 `metrics.compute_metrics` 为 TWR 模拟器/账本类** | metrics.py 大改 + 手算抽查用例保留 | 1647 行单函数是交割单模块最大维护痛点；先补行为快照测试再拆。 |
| P2-7 | **解除后端测试对 `backend.main` 私有名耦合**（删 re-export 块 + `_host` 间接层） | main.py/jobs.py + 11 个测试文件 | 测试耦合实现细节阻碍任何拆分；改为依赖注入后 backend 可安全重构。 |
| P2-8 | **`graph.py` 正则下单三段合一**（抽 `parse_plan_lines` 纯函数 + 单测） | graph.py 重构 + 新单测 | 消除 3 段重复，LLM 输出格式变更时只改一处；附带消除静默漏单。 |
| P3-9 | **前端清理**：删 `getReviewDetail` 死导出、修 TradesTab/ChatTab 缩进、MOCK_STEPS 分析师名对齐、删 App `view==="chat"` 不可达分支 | api.js/2 组件/App.jsx，低风险 | 死代码与错乱缩进是维护噪音，可批量完成。 |
| P3-10 | **数据层按块真实拆分 stock_data.py**（承接 P0-1：删 stub 后按 10 个数据块落地） | stock_data.py 大拆 + 全量回归 | 最大工程但最必要；建议在 P1-3 文档定稿 + 回归脚本全绿后单独立项做，勿与功能开发并行。 |

> 另注（非本次动作）：仓库根 80 个 `pytest-cache-files-*` 目录、`_call_with_timeout` 幽灵线程模式、tdx 主站轮换超时超契约（30s）三项建议列入日常清理/巡检项。

---

*本报告为只读审计产物；所有结论基于静态代码阅读，涉及运行时行为（线程泄漏、mock 顶替、并发丢消息等）均标注「待确认」，建议以 `scripts/regression.py` 全量回归 + 冒烟验证后作为重构基线。*
