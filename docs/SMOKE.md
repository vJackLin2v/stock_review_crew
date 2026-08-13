# 真实数据冒烟记录（2026-08-03）

> 服务：`uvicorn backend.main:app`（.venv Python 3.12 / chroma 1.5.9），端口 8501；
> 代理：127.0.0.1:7890；LLM：opencode.ai/zen/go/v1（deepseek-v4-flash，真实调用）。
> 冒烟脚本：`python scripts/smoke_review.py <date> <mode>`。

## 复盘模式冒烟

| # | 日期 | 模式 | 耗时 | 数据源 | 降级标注 | 结果 |
|---|---|---|---|---|---|---|
| 1 | 2026-07-31 | pre_market 早盘前决策 | 199s | 通达信akshare / 财新/央视/经济日历 | 外盘/期货仅当天可用（历史补做） | ✅ 报告 9176 字 |
| 2 | 2026-07-31 | auction 竞价复盘 | 90s | —（全链缺失） | 开盘啦无 Cookie→同花顺 404→东财推算空 | ✅ 流程完整，报告明确标注「竞价数据全链路缺失」 |
| 3 | 2026-07-31 | intraday_am 上午盘中 | 179s | 东财 / 本地缓存 | 东财 502（网络不稳）、通达信本地无分钟线 | ✅ 报告 4467 字 |
| 4 | 2026-07-31 | noon 午间复盘 | 259s | 东财 / 本地缓存(旧版) | 东财 502、Tushare 沙箱 tk.csv 写权限、通达信本地陈旧 | ✅ 报告完整 |
| 5 | 2026-08-03 | intraday_pm 下午盘中 | 235s | 东财（涨停池等） | 东财分钟/板块 502、THS mini_racer 禁用 | ✅ 真实 LLM 全流程 7 阶段，报告 5881 字 |
| 6 | 2026-08-03 | close 收盘复盘 | 382s | 通达信akshare / 东财 | 东财分钟/板块 502、Tushare 沙箱、THS、lhb 东财解析 NoneType | ✅ 真实 LLM，报告 7535 字 |

## 功能验证

| 项目 | 结果 |
|---|---|
| 窗口校验 | ✅ 14:45 选 auction → 400「竞价数据已结束，建议切换上午盘中或午间复盘」；15:00 前选 close → 400 建议下午盘中 |
| 参数校验 | ✅ 非法模式/非法日期 → 400 中文 |
| 竞价数据层 | ✅ 东财 HTTPS→HTTP 改写后：`_em_zt_pool` 99 只涨停（0.3s）、`_em_pre_min` 返回 09:15-09:25 真实竞价分时 16 行（仅当日）；完整竞价流程待窗口内复跑 |
| 聊天 | ✅ 多分析师交叉（阿狼+养家，65.8s）：1805 字回复含逐位观点+汇总；响应恒含 disclaimer 字段、文尾免责声明；按标的过滤/详情/历史 全通过 |
| 历史存储 | ✅ 6 份冒烟记录全部落盘 `data/reviews/{date}/{time}/`；旧 output/ 16 份导入冒烟通过（幂等） |
| 前端 | ✅ `npm run build` 通过；normalize 断言通过；headless Chrome 全流程冒烟（含进度条/报告/聊天/侧栏） |
| 全量测试 | ✅ `python -m pytest` 190 passed（离线确定性，进程干净退出） |

## 环境性降级说明（非代码缺陷，正常环境会改善）
- **东财**：本机网络对 eastmoney TLS 做 SNI 层 Reset → 已实现 HTTPS→HTTP 改写 + 多子域轮换重试；偶发 502 由降级链标注。
- **Tushare**：沙箱禁止写 `C:\Users\LIN\tk.csv` → 正常环境可用；token 已换新（2026-08-03）。
- **同花顺**：行业接口依赖 py_mini_racer（Windows IOCP 退出死锁）→ 已禁用并走东财备用；如需恢复需解决退出挂起。
- **F:\tdx**：本地日线截至 2025-05-09、minline 为空 → 仅作降级源。
- **竞价**：开盘啦仅 App 端（无网页版），需抓包 Cookie；当前以东财盘前分时为主源（仅当日），历史竞价标注缺失。

## R6 盘中数据（2026-08-04）

> 验证时间：2026-08-04 12:35（午间休市，行情为 12:05 午休快照）；代理 127.0.0.1:7890；
> 运行：`.venv` Python（3.13）+ 代理环境变量，脚本纯 ASCII。
> 以下行情数据仅供技术冒烟记录，仅供参考，不构成投资建议。

| 函数 | 耗时 | 关键数值 | 结论 |
|---|---|---|---|
| `realtime.fetch_sentiment_realtime('2026-08-04')` | 0.12s | yesterday=2026-08-03；yesterday_zt_count=75、matched_today=75（东财昨日涨停池全量命中新浪实时）；avg_return=0.041185（+4.12%）、red_rate=0.826667（82.7%）、lianban_rate=0.213333（16 只再涨停）、hean_rate=0.0（0 只核按钮）；best3：欣天科技 +0.2004、达实智能 +0.1013、新能股份 +0.1007；worst3：锦和商管 -0.0530、凯迪股份 -0.0406、裕兴股份 -0.0248；source=东财昨日池+新浪实时 | ✅ 通过。degraded=True 属设计内标注（Tushare 收盘口径说明），note 含「盘中实时口径（昨收为基准）」；比率均为小数 |
| `realtime.fetch_breadth_realtime()` | 0.17s（失败） | 无返回。`RealtimeError: HTTP 请求失败：502 .../api/qt/clist/get`；追加探测：push2/80.push2/17.push2 三子域各重试 3 次全 502，pz=100/1000/5000/7000 均 502，HTTPS 经代理 RemoteDisconnected；同域 `ulist.np/get` 指数接口正常 | ❌ 失败（环境性）。与 PLAYBOOK「[2026-08-04] 东财 push2 全系 502（代理节点问题）」一致，仅 clist 路径受影响；实时层单源无备用，显式抛中文错误、未静默给数据（符合铁律 7）；收盘口径降级链在 `stock_data.fetch_market_breadth`（Tushare→乐咕→缓存），本轮未触发 |
| `stock_data.fetch_index_trend('2026-08-04')` | 41.16s | 主源通达信 akshare（degraded=False，日线 asof=2026-08-03）；R6-1 并入东财 ulist 实时（realtime_source=东财实时）：上证 3815.59/+0.0016、深成 13817.39/+0.0274、创业板 3462.05/+0.0483、科创50 1598.13/+0.0291；note 含「当日点位为盘中实时（R6-1）」 | ✅ 通过。close/pct_change 与独立 ulist 原始请求（含 pre_close 3809.66/13448.29/3302.55/1552.89）及腾讯 qt.gtimg.cn（12:05 时间戳）逐位一致，确为当日盘中值；小瑕疵：note 前缀「非交易日」按日线 asof 判定（午休时日线尚未含当日 bar），当日实为交易日，不影响盘中值正确性 |

备注：
- PS 5.1 传参坑：多行 here-string 传给 `python -c` 时，参数内双引号会被剥掉并截断脚本（实测 `strip('"')` 被改成 `strip('')` 后截断）；本轮脚本用 `chr(34)/chr(126)` 规避，后续冒烟脚本应避免在 `-c` 代码中出现双引号。
- 本轮仅验证 R6 三个实时函数；`fetch_breadth_realtime` 待代理节点恢复后复跑（同窗口 13:00 后亦可复测下午盘中值）。

## R8 聊天智能流程（2026-08-04）

> 验证时间：2026-08-04 16:46；服务：.venv uvicorn 8501；代理 127.0.0.1:7890；LLM 真实调用（双分析师）。
> 以下聊天内容仅供技术冒烟记录，仅供参考，不构成投资建议。

| 项目 | 结果 |
|---|---|
| 6 步事件序列 | ✅ stage(parse→need→fetch) → analyst_start/analyst_delta*(流式) → analyst_end×2 → stage(summarize) → summary_delta*(流式, 316 字) → summary_end → done |
| parse 解析（真实 LLM） | ✅ `{intent: trend, target: 600519, time_range: 近5日}` |
| 数据需求与取数 | ✅ needs 8 项 data_key 均在白名单；降级如实标注（minute_kline→腾讯分时、news→央视无数据、sectors→腾讯行业排行、news_headlines→央视无数据） |
| 数字核验 | ✅ 收盘 1328.36 / 跌 2.25% / 开 1350.06 / 高 1350.94 / 换手 0.3% 与腾讯实时行情逐位一致；verify_numbers 13 项精确命中（跨主体数字误报为工具已知限制，非 LLM 幻觉） |
| 合规 | ✅ 消息尾部恒含免责声明；stage 事件仅含提示、不携带需求清单（Q7）；分析缺失数据时明确说明（近 5 日 K 线不全等） |
| 耗时 | 121.6s（双分析师真实 LLM 流式，含 4 次 LLM 调用 + 多块取数） |
