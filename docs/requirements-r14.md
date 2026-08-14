<!-- 已合并进 requirements.md，本文档保留供追溯，勿再新增改动（2026-08-13） -->
# R14 复盘批次 × 账户批次两段式跑批 — 需求分析文档（已确认版）

> 2026-08-11 提出。用户已确认核心决策（见 §三），本文为开发契约；
> 硬约束：全量 pytest 与一键回测（scripts/regression.py，当前 17 项）必须全绿；
> 报告 Markdown 正文、R12 账户数据口径（data/accounts/*）保持不变。

## 一、目标

把「模拟账户跑批」重构为**两段式**：

1. **复盘批次（LLM 环节）**：每个交易日一份复盘，产出结构化交易计划
   `data/reviews/{date}/plan.json`（**唯一真源**）。单日可重跑、可覆盖。
2. **账户批次（确定性环节）**：只消费 plan.json + 行情缓存，全量重放撮合
   （不调 LLM），产出 `data/accounts/{skill_id}/`（trades/snapshots/account）。

某天复盘数据有问题 → 只补该日复盘 → 账户全量重放（快、确定性、可离线）。

## 二、现状问题（重构动机）

- 复盘与结算在逐日脚本（work/run_aug.py、july_backfill.py）里交错执行，无独立入口；
- 交易计划 4 路写入（LLM 工具、graph 内 3 种正则、backfill_pending 另套正则），
  曾出现单日单股 4 条重复 pending 的 bug；格式漂移会漏提取；
- 复盘产物里没有结构化计划文件，推荐散落在自然语言与 pending.json；
- 账户无全量重放能力，补某天后连锁影响（持仓/现金）无法干净重建；
- scripts/backtest_accounts.py 的规则引擎回测与复盘驱动账户两套并行，口径分裂；
- 结算现抓行情（慢、依赖网络），无行情缓存。

## 三、已确认决策（用户 2026-08-11）

| # | 决策 | 结论 |
|---|---|---|
| 1 | 计划唯一真源 | 复盘结束统一落 `data/reviews/{date}/plan.json`；账户批只读它，不再做任何正则/文本解析 |
| 2 | 补复盘连锁 | 账户**全量重放**（方案 A，确定性快）；旧账本可被覆盖 |
| 3 | 行情缓存 | 要：`data/quotes_cache/{date}.json`，重放离线可用 |
| 4 | 历史复盘 | 不迁移：先校验（报告完整/无 LLM 降级/计划完整），不合格直接重跑 |
| 5 | 旧回测脚本 | scripts/backtest_accounts.py 并进新架构（合规断言保留），规则引擎回测不再作为生产路径 |

## 四、数据字典（唯一真源）

### 4.1 plan.json — `data/reviews/{date}/plan.json`

| 字段 | 定义 | 公式/来源 | 边界 | None 语义 |
|---|---|---|---|---|
| date | 复盘日 | 目录名 | YYYY-MM-DD | — |
| mode | 复盘模式 | 固定 "close"（批跑仅收盘模式） | close | — |
| generated_at | 生成时间 | ISO | — | — |
| plans[] | 交易计划列表 | 复盘引擎工具调用 + 正则提取，去重后落盘 | 可为空数组 | 空=该日无推荐 |
| plans[].rec_date | 推荐日期（复盘日） | = date | — | — |
| plans[].skill_id | 分析师 id | skills 目录 | 排除 host/review_assistant | — |
| plans[].skill_name | 分析师名 | skills 目录 | — | — |
| plans[].code | 股票代码 | 6 位数字 | zfill(6) | — |
| plans[].name | 股票名 | 行情/分析师提供 | 缺失用 code | — |
| plans[].side | 方向 | buy/sell | 其他拒绝 | — |
| plans[].target_price | 目标价（元） | 分析师提供 | >0 | — |
| plans[].position_cheng | 仓位成数 | 1-10 | 工具传 0 时用 qty | 0=未用成数 |
| plans[].qty | 具体股数 | 可选 | >0 整数 | null=按成数折算 |
| plans[].source | 来源 | tool/regex_qty/regex_cheng | 枚举 | — |

去重键：`(rec_date, skill_id, code, side, target_price)`，重跑复盘整文件覆盖。

### 4.2 行情缓存 — `data/quotes_cache/{date}.json`

| 字段 | 定义 |
|---|---|
| date | 行情日 |
| fetched_at / source | 抓取时间 / 降级链来源标注 |
| quotes[code] | {name, pre_close, high, low, close}；缺失代码**不在文件里**（而非 None） |
| degraded | 该日取数降级/缺失标注（铁律 7，绝不静默） |

> 缓存语义（2026-08-14 修订）：`ensure_quotes(date, codes)` 对**已命中**的代码以缓存为
> 权威（不重抓、保持确定性）；对**缺失**代码逐 code 补抓并**合并落盘**（历史日期主源
> 失败仍拒绝腾讯实时价兜底）。此前「缓存文件存在即整体权威返回」会把部分快照外的
> 计划股行情挡在缓存外，导致逐日撮合 0 成交。

### 4.3 账户产物（沿用 R12，不变）

`data/accounts/{skill_id}/`：account.json（现金/持仓/冻结）、trades.json（成交+拒绝全记录）、
snapshots.json（每日资产）。撮合口径冻结（R12 手算样例：10 万×3 成 target=10.00 → 2900 股、
费用、次日卖出 2900×10.50 → 现金 101,419.318）。

### 4.4 重放报告

`.tmp/account_replay/`（不写仓库 data/）：replay_report.md（每分析师汇总/违规清单/拒绝原因分布）、
replay_meta.json（输入指纹：plan.json 哈希、行情缺失标注、未结算 pending 数）。

## 五、API 契约

- **无新增 HTTP API**（批跑为 CLI；GET /api/accounts 等现有接口不变）。
- CLI：
  - `python work/review_batch.py run [--start D] [--end D] [--dates d1,d2] [--force] [--max-rounds N]`
    —— 提交复盘 job→轮询→产物校验（report+plan 存在、无 LLM 降级）→不合格重试≤3。
  - `python work/review_batch.py validate [--start D] [--end D]`
    —— 校验已落盘复盘，输出 {date, ok, reasons[]} 与需重跑清单（JSON + 控制台）。
  - `python work/account_replay.py [--start D] [--end D] [--rebuild] [--fixture]`
    —— 全量重放；退出码 0=无违规，1=发现引擎漏洞/断言失败，2=数据不可用。

## 六、架构与文件所有权（任务拆分）

| 模块 | 内容 | 文件所有权 | depends_on |
|---|---|---|---|
| A1 计划层 | accounts/plan.py（PlanRec/去重/校验/registry+finalize/读写 plan.json）；graph.py place_order 工具改为写计划 registry；后端 `_run_review_job` 或 graph 收尾处 finalize | src/stock_review_crew/accounts/plan.py（新）、graph.py、backend/main.py（仅挂钩） | 无 |
| A2 行情缓存+重放 | accounts/quotes.py（fetch_day_quote 迁移+缓存读写）；accounts/replay.py（全量重放/合规断言/报告；吸收 backtest_accounts 的 AssertionChecker/ComplianceReporter） | src/stock_review_crew/accounts/quotes.py（新）、replay.py（新） | 无 |
| A3 批跑脚本+并轨 | work/review_batch.py、work/account_replay.py（新）；删除 scripts/backtest_accounts.py；旧 work/*.py 归档 work/archive/ | work/ 新文件、scripts/backtest_accounts.py、work/ 旧脚本 | A1、A2 |
| A4 测试先行 | tests/test_account_plan.py、test_quotes_cache.py、test_account_replay.py；改造 test_r12_backtest.py（指向 replay）、test_pending_dedup.py（指向 plan 去重） | tests/ | 无（只看需求文档） |
| A5 质量基线 | rubric 评分/数字核验清单、验收用例审计（docs/quality-r14.md）；集成阶段校验文件越界 | docs/quality-r14.md | 无 |

## 七、验收标准（业务逻辑硬门）

1. 复盘完成后 plan.json 生成；同日重跑**整文件覆盖**，无残留旧计划；
2. 相同 plan.json + 相同行情 → 两次全量重放结果逐字节一致（确定性）；
3. 补单日复盘闭环：D 日重跑 → plan.json 更新 → 重放后 D+1 起账户正确（引擎规则手算样例复现）；
4. 重放离线可用（行情全部命中缓存）；缺失行情如实标注，绝不静默错报；
5. scripts/backtest_accounts.py 移除后全量 pytest 仍绿；违规注入仍被断言抓到；
6. validate 子命令正确标记 可用/需重跑（报告缺失/LLM 降级/计划为空）；
7. 全量 pytest（≥534 passed）+ 一键回测 17/17 全绿。

## 八、口径假设（默认采用，如不同意请指出）

- 交易日序列：复盘批按工作日生成（周末跳过，节假日表不内置，可 --dates 显式指定）；
  账户重放的结算日序列 = 已存在复盘产物的日期集合（自洽，规避节假日问题）；
- 推荐撮合语义不变：rec_date=D 的计划用 D 之后最近交易日行情撮合，未到结算日的保持待结算并在报告中计数；
- 规则引擎降级日不产生计划（plan.json 空数组 + meta.degraded 标注，校验判为需重跑）；
- 旧 work 脚本移入 work/archive/（保留参考，不删除），测试引用同步改指向新模块。

## 九、风险

- 历史补跑依赖 LLM 可用性与网络（opencode 端点 + 代理，PLAYBOOK 有排查法）；LLM 不可用时
  validate 标记需重跑、批跑如实降级，绝不静默；
- 行情源故障（东财 502 等）：quotes.py 复用现有降级链（东财→腾讯→通达信→本地），缓存标注降级；
- graph 五分析师并发写计划：registry（线程安全）+ 收尾 finalize 单点落盘，避免文件并发写；
- pyarrow 段错误等进程级故障：沿用守护脚本自愈，批跑脚本失败单日重试不中断。
