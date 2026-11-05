# Stock Review Crew — A 股多智能体复盘系统

全量重构版（2026-08-03）：FastAPI 后端 + React/Vite/ECharts 前端 + LangGraph 分析师流程，
支持 **6 种时间模式复盘** 与 **分析师聊天**。需求契约见 `docs/requirements.md`（数据字典唯一真源）。

## 功能

- **6 模式复盘**：早盘前决策 / 竞价复盘 / 上午盘中 / 午间复盘 / 下午盘中 / 收盘复盘；
  模式决定取数与提示词，历史日期可补做，数据缺失自动降级标注；
- **分析师聊天**：与 5 位分析师（阿狼/爱在冰川/拔小弦/炒股养家/铁锤）单人或多人问答；
  聊天走智能 6 步流水线（问题解析 → 分析师数据需求 → 取数 → 基于真实数据分析 → 综合总结流式），
  过程显示「当前步骤」提示，取数失败如实告知缺失项，免责声明程序级强制；
- **历史存储**：`data/reviews/{日期}/{时间}/`（meta+报告+快照），同日多份，支持删除；
- **Codex 桌面风格前端**：深色侧栏、分步进度条、红涨绿跌、ECharts 图表、全中文。

## 快速开始

### 一键启动（推荐）

```text
双击项目根目录：
  「启动服务.bat」  → 本地服务：自动启动后端并打开 http://127.0.0.1:8501
  「启动隧道.bat」  → 本地服务 + Cloudflare 公网隧道（打印公网地址）
```

### 命令行启动

```powershell
# 启动后端（.venv；数据源走本机代理 127.0.0.1:7890）
$env:HTTP_PROXY='http://127.0.0.1:7890'; $env:HTTPS_PROXY='http://127.0.0.1:7890'
H:\stock_review_crew\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8501
```

打开 http://127.0.0.1:8501（端口统一为 8501，与 `AGENTS.md` 一致）。
开发前端：`npm run dev`（根目录已代理到 `frontend`，无后端时自动降级 mock）；
前端构建：`npm run build`（根目录执行即可）。

## 测试与冒烟

```powershell
python -m pytest -p no:cacheprovider -q
# 全量 pytest 实测数：491 passed（2026-08-08，系统 Python 3.13，无环境性失败）
```

真实数据冒烟：`python scripts/smoke_review.py <YYYY-MM-DD> <mode>`（记录见 `docs/SMOKE.md`）。

## 架构

```
backend/main.py            FastAPI：复盘任务(异步+进度轮询) / 历史 / 聊天 / 静态托管
src/stock_review_crew/
  graph.py                 6 模式感知 LangGraph（取数→资讯→趋势派→情绪派→主持人→辩论→报告）
  prompts.py               6 模式提示词模板 + 主持人/复盘助手角色
  chat.py                  聊天引擎（单/多分析师交叉，免责声明强制）
  quality.py               复盘/聊天质量评估（Langfuse trace + 数字核验 + rubric 打分）
  tools/stock_data.py      数据层：6 模式取数、超时≤30s、降级链、source/degraded 标注
  tools/market_extra.py    R10 市场扩展：市场天气/情绪温度、纳斯达克、额外指数、风格坐标、板块温度榜
  tools/tdx_local.py       通达信本地数据只读解析（F:\tdx）
  storage/reviews.py       历史复盘存储（同日多份/删除/上下文注入）
  storage/chats.py         对话存储
  storage/watchlist.py     观察列表（R10：增删改排，data/watchlist.json）
  knowledge_store.py       ChromaDB 历史复盘/对话检索
frontend/                  React + Vite + ECharts（Codex 桌面风格）
skills/                    5 位分析师 + 主持人 + 复盘助手（JSON 人格）
```

## 数据源与降级

| 数据 | 主源 | 备用 | 备注 |
|---|---|---|---|
| 指数日线/均线 | 通达信 akshare | 通达信本地 | 长历史 |
| 涨停/跌停/梯队 | 东财 | 同花顺/Tushare | 约 30 交易日 |
| 板块涨幅/资金 | 同花顺 | 东财 | mini_racer 禁用时走东财 |
| 分时/分钟线 | 东财 | 通达信本地 | 东财 HTTPS 已被改写为 HTTP |
| 竞价数据 | 东财盘前分时（当日） | 开盘啦(需 Cookie) | 历史竞价标注缺失 |
| 情绪指标 | Tushare | 东财/通达信本地 | — |
| 龙虎榜 | 东财 | Tushare | — |

降级链：主源 → 备用 → 本地缓存 → 明确标注「数据缺失/估算」，绝不静默错报。

## 环境变量（.env，不入 git）

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` | opencode 网关（https://opencode.ai/zen/go/v1） |
| `TUSHARE_TOKEN` | Tushare Token |
| `KAIPANLA_COOKIE` | 开盘啦登录 Cookie（可选，App 抓包获取） |
| `TDX_PATH` | 通达信安装路径（F:\tdx） |

## 文档

- 需求契约：`docs/requirements.md`；踩坑手册：`docs/PLAYBOOK.md`
- 进度与 agent 登记：`docs/PROGRESS.md`；issue 文本：`docs/issues.md`
- 冒烟记录：`docs/SMOKE.md`；审计报告：`docs/audit-report.md`（Wave 3）
