# 优化执行计划（Wave 1）— 2026-08-07

> 依据：2026-08-07 四 Agent 并行分析产出的问题清单（UI / 数据展示 / 复盘与提示词 / 冗余功能）。
> 本文件为 Wave 1 任务拆分与文件所有权契约（唯一真源）。只处理**证据确凿、无口径分歧**的修复；
> 涉及产品决策或大型重构的项列入「待确认」，不在本轮处理。
> 铁律：禁止 git 操作；禁止真实数据写入仓库；测试用系统 `python -m pytest -p no:cacheprovider`；
> 改动后必须跑相关测试；集成阶段跑一键回测（全量 pytest + 前端 build + 契约校验）。

## 一、Wave 1 范围

| 编号 | 问题 | 级别 | 负责 Agent | 验收标准 |
|---|---|---|---|---|
| P0-1 | 6 模式差异化提示词死代码（mode focus/judgment/plan 未注入分析师模板） | P0 | A | `build_analyst_prompt` 输出含本模式分析要点；pre_market 不再写「明日个股推荐」 |
| P0-2 | 涨跌分布 9 档键名前后端不一致（真实数据静默丢 6 档） | P0 | B | 真实键名与前端 BREADTH_ORDER 对齐；verify-normalize 断言补键名校验 |
| P0-3 | 侧栏「复盘记录」重试死按钮 | P0 | C | 点击可重新加载历史 |
| P1-A | 报告 prompt 单位铁律与金额换算指令自相矛盾 | P1 | A | 指令分层明确，无冲突措辞 |
| P1-B | 报告无长度上限/去重/防废话指令 | P1 | A | 报告 prompt 含总字数上限与禁止复制原文约束 |
| P1-C | 降级模式必现 3 轮内容相同假辩论 | P1 | A | 降级时辩论 ≤1 轮或按立场判分歧，无逐字重复段落 |
| P1-D | 辩论环节 5 人串行调用 | P1 | A | debate_node 轮内并行 |
| P1-E | close「跌停板」恒显数据缺失（嵌套结构取不到） | P1 | A | 跌停数据进入 market_data 与规则版报告 |
| P1-F | close 关键闸门卡在几乎不渲染的 minute；intraday_pm 龙虎榜声明未取数 | P1 | A | close 关键块不再含 minute；intraday_pm 删除龙虎榜块声明（盘中本无数据） |
| P1-G | 报告 prompt 未注入昨日/更早复盘上下文 | P2 | A | build_report_prompt 含昨日/当日更早复盘节 |
| P1-H | 分布降级静默空图（note 被丢弃） | P1 | B | 快照透传 note；前端空态显示「数据缺失」标注 |
| P1-I | 首页多处 None 显示 0.00 / 0.0 亿（违反铁律 6） | P1 | B | 降级场景首页显示「—」 |
| P1-J | 情绪柱图颜色语义误导（炸板率/核按钮率用红涨色） | P2 | B | 比率类指标用中性/温度色 |
| P1-K | 报告板块图缺跌幅榜（bottom5 被丢弃） | P2 | B | 快照/normalize 透传 bottom5（渲染可后续） |
| P1-L | 梯队图龙头名单已取未展示 | P2 | B | tooltip 显示个股名单 |
| P1-M | 成交额/量单位口径混用 | P2 | B | 统一智能缩写（万/亿），五档量带「手」 |
| P1-N | 涨跌分布双标题（卡中卡） | P1 | B | 单标题 |
| P1-O | 首页删除确认 window.confirm 与侧栏不一致 | P2 | B | 统一内联确认 |
| P1-P | mock 报告「涨跌家数比」显示 156% | P3 | B | 显示 1.56 : 1 |
| P1-Q | 后台任务完成静默替换正在阅读的报告 | P0 | C | 仅当用户在看进度或同一份报告时才替换 |
| P1-R | 报告无目录/锚点/返回顶部 | P1 | C | 标题目录可点击跳转 |
| P1-S | 报告/聊天无复制导出 | P1 | C | 复制 Markdown/纯文本按钮可用 |
| P1-T | 移动端会话切换路径过深 + 761–860px 断点突隐 | P1 | C | 聊天页内可切换会话；断点对齐 |
| P1-U | 聊天会话列表加载态永不出现（sessionsLoading 恒 false） | P2 | C | 加载中显示 loading 态 |
| P1-V | 创建会话失败无重试入口 | P2 | C | 错误面板可重新发起 |
| P1-W | 同日多份报告无法对比切换 | P2 | C | 报告页同日时间点切换条 |
| P1-X | 观察列表 📌 置顶半成品 | P1 | C | 置顶/取消置顶可用或移除 📌 |
| P1-Y | MarkdownView 表格无分隔行时丢首行 | P2 | C | 首行不丢 |
| P1-Z | 图标按钮 aria-label、favicon/theme-color | P2 | C | 可访问性补齐 |
| P1-AA | 死样式 .new-icon、死导出 normalizeHealth/sendChatMessage/localIso/seedMockWatchlist/fmtNone/fmtMonth | P2 | B/D | 清理后 rg 零引用 |
| P1-AB | 一次性脚本 rerun_7.py / batch_review.py | P2 | D | 删除前 rg 确认无引用 |
| P1-AC | 文档归档（draft/v2/v3/v5/langgraph-build-guide/research/REALTIME） | P2 | D | 移入 docs/archive/ |
| P1-AD | README 过时（测试数/端口 8502→8501/架构图补 R10） | P2 | D | 修订完成 |

## 二、待确认项（本轮不处理）

- 情绪周期结构化输出与主线/梯队章节设计（P1-18/19）——需产品口径；
- 聊天「多分析师交叉表态」实现（requirements.md §1.4 与 v4 矛盾）——需先定需求；
- 板块聊天按 target 过滤取数（P1-12）——取数键设计；
- 双轨降级链合并、HTTP 助手合并、_tx_symbol 映射合并——重构，单独排期；
- 旧 Streamlit 入口岛（app.py + src/main.py + output/ + streamlit 依赖）删除——需用户确认；
- quality 评分前端展示——需持久化方案设计；
- 英文嵌入模型替换 bge-small-zh——依赖安装需提权；
- 荐股程序化校验（tool_used 拦截）——设计验证规则。

## 三、Agent 文件所有权（互斥，写入仅限自己名下）

| Agent | 写权限文件 | 修复项 |
|---|---|---|
| A | src/stock_review_crew/prompts.py、src/stock_review_crew/graph.py、tests/test_prompts.py、tests/test_graph.py、tests/test_quality.py（仅断言同步） | P0-1, P1-A..G |
| B | src/stock_review_crew/tools/stock_data.py、backend/main.py、frontend/src/normalize.js、frontend/src/format.js、frontend/src/mock.js、frontend/src/components/Charts.jsx、frontend/src/components/HomeView.jsx、frontend/src/components/StockDetail.jsx、frontend/scripts/verify-normalize.mjs、tests/test_backend.py、tests/test_data_*.py（相关） | P0-2, P1-H..P, P1-AA(前段) |
| C | frontend/src/App.jsx、frontend/src/components/Sidebar.jsx、frontend/src/components/ChatView.jsx、frontend/src/components/ReportView.jsx、frontend/src/components/MarkdownView.jsx、frontend/src/components/WatchlistPanel.jsx、frontend/src/styles.css、frontend/index.html | P0-3, P1-Q..Z, P1-AA(样式) |
| D | scripts/rerun_7.py（删）、scripts/batch_review.py（删）、frontend/src/api.js（仅删 sendChatMessage）、docs/（归档+README+PROGRESS 追加）、README.md | P1-AB..AD |

## 四、集成闸门（主 Agent 执行）

1. 全量 `python -m pytest -p no:cacheprovider` 全绿；
2. `npm run build` 成功；`npm run verify:normalize` 全绿；
3. 契约抽查：涨跌分布键名、报告 prompt 占位符、侧栏重试接线；
4. `scripts/regression.py` 一键回测（环境允许时）；
5. PROGRESS.md 追加本轮回测记录；PLAYBOOK 有踩坑则追加。
