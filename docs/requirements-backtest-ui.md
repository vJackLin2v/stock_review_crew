# 回测可视化功能（需求文档）

> 状态：已与用户对齐（4 项决策已确认）。数据字典唯一真源 `docs/requirements.md` §六。
> 本文定义「历史回测」的可视化编排功能：页面选范围 → 后端逐日跑复盘+撮合 → 实时看进度与耗时。

---

## 一、背景与目标

- 现状：单日复盘是后端 job（有 stage 进度）；多日批跑是 `work/review_batch.py` 客户端脚本，无后端编排、无可视化、无精确耗时。
- 目标：在页面新增独立「回测」tab，用户可选回测范围、一键启动、实时查看进度，并**精确到每天耗时、每个节点（取数/分析师/主持人/报告）耗时、每个分析师耗时**。

## 二、已确认的 4 项决策

1. **独立「回测」tab**（侧边栏新增入口，不进 ReviewTab）。
2. **列表 + 折线图都要**：每天耗时的可展开列表 + 每天耗时趋势折线图。
3. **落盘持久化**：回测进度/结果写 `data/backtests/{id}/`（重启可查历史）。
4. **细分到每个分析师耗时**：`analysts` 节点下拆出 5 位分析师各自的耗时。

## 三、数据模型（进度 + 耗时，唯一契约）

```json
{
  "id": "bt_20260815_103000",
  "status": "running",              // queued | running | done | error | cancelled
  "start": "2026-07-01", "end": "2026-08-14",
  "params": {"force": false, "max_rounds": 3, "mode": "close"},
  "created_at": "2026-08-15T10:30:00", "finished_at": null,
  "days_total": 33, "days_done": 10,
  "current": {"date": "2026-07-14", "stage": "analysts"},
  "total_elapsed_ms": 1234567,
  "days": [
    {
      "date": "2026-07-14",
      "status": "done",             // pending | running | done | failed | skipped
      "attempt": 1, "attempts": 1,
      "elapsed_ms": 267000,          // 当天总耗时 = settle + review
      "settle": {"elapsed_ms": 3200, "filled": 3, "rejected": 2, "quotes_missing": 0},
      "review": {"elapsed_ms": 263800},
      "stages": [
        {"key": "fetch",    "elapsed_ms": 18400},
        {"key": "analysts", "elapsed_ms": 198000,
         "analysts": [
           {"skill_id": "alang", "name": "阿狼", "elapsed_ms": 21000},
           {"skill_id": "bingchuan", "name": "爱在冰川", "elapsed_ms": 19000}
         ]},
        {"key": "host",   "elapsed_ms": 21000},
        {"key": "report", "elapsed_ms": 26200}
      ],
      "error": null
    }
  ]
}
```

- 阶段 key 固定：`fetch` / `analysts` / `host` / `report`（资讯默认关，无 `news`）。
- 每天耗时 = `elapsed_ms`；节点耗时 = `stages[].elapsed_ms`；分析师耗时 = `stages["analysts"].analysts[]`。

## 四、API 契约（前后端并行开发的冻结契约）

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | `/api/backtest/start` | `{start, end, force?, max_rounds?, mode?}` | `{backtest_id}` |
| GET | `/api/backtest/{id}` | — | §三 完整对象 |
| POST | `/api/backtest/{id}/cancel` | — | `{ok: true}` |
| GET | `/api/backtest/history` | — | `{runs: [{id, start, end, status, created_at, finished_at, days_done, days_total, total_elapsed_ms}]}` |

- 单次只允许一个 running 回测（再次 start 返回 409 中文，或排队）。
- 非法日期/范围 → 400 中文；历史回测按 created_at 倒序，上限 20 条。

## 五、耗时采集（关键实现点）

1. **阶段耗时**：增强 `backend/jobs.py` `_progress_cb`——每次 `stage` 变化时，给上一 stage 结算 `elapsed_ms`、给新 stage 记 `started_at`（`time.monotonic()`），写入 job 的 `stage_timings`。
2. **分析师耗时**：`graph.py` 分析师线程池（`_run_analysts`）在执行每个分析师前后，通过进度回调上报 `analysts` 子事件（带 `skill_id` + started/finished），由回测编排器的回调汇总成 `stages["analysts"].analysts[]`。
3. **settle/review 两段耗时**：回测编排器在每天 `settle_plans` 前后、`review` 前后打点。
4. 所有耗时用 `time.monotonic()`（ms 精度，跨日稳定）。

## 六、后端编排器 `backend/backtest.py`

1. 把 `work/review_batch.py` 的逐日循环搬进后端后台线程：生成交易日列表（周末跳过）→ 逐日 `settle_plans(prev, day)` → `review(day)` → 校验 → 记耗时。
2. **复用单日复盘逻辑**：把 `backend/jobs.py::_run_review_job` 的核心抽成可复用函数 `run_review_day(date, mode, max_rounds, trace, progress_cb)`，单日 job 与回测循环共用（避免重复实现）。
3. **fail-fast**：单日重试 MAX_ATTEMPTS 次仍失败 → 中止批次、status=error（对齐 `review_batch.py` fail-fast 语义，绝不静默跳过）。
4. **取消**：`cancel` 置标志位，当日完成后停止后续日期，status=cancelled。
5. **落盘**：`data/backtests/{id}/manifest.json` + `progress.json`（每次阶段/天变化时写；尊重 `STOCK_REVIEW_DATA_DIR` 隔离）。
6. 断点续跑不做（回测是重跑性质，不需要跨进程续跑）。

## 七、前端 UI（独立「回测」tab）

1. 侧边栏新增「回测」入口 + 新组件 `BacktestView.jsx`。
2. 顶部：起止日期选择器 + 开始按钮（force / max_rounds 选项）。
3. 进度区：
   - 总进度条（days_done/days_total）+ 当前日期/阶段 + 总耗时；
   - **折线图**：每天 `elapsed_ms` 趋势（复用前端现有图表方案）；
   - **每天一行列表**：日期 / 状态 / 当天耗时 / settle 成交·拒绝 / review 耗时 / 失败原因；
   - 每行可**展开**：settle 耗时 + 4 个节点耗时 + 5 位分析师各自耗时（横向条形或列表）。
4. 实时轮询 `GET /api/backtest/{id}`（2s），终态停止轮询。
5. 历史：进入页面时拉 `GET /api/backtest/history`，可点开历史某次回测看进度。

## 八、验收标准（逐条可验证）

1. 页面侧边栏有独立「回测」tab，可进入。
2. 可选起止日期并启动；启动后可见总进度、每天耗时、每个节点耗时、每个分析师耗时。
3. 折线图正确反映每天耗时趋势；每天列表可展开看节点/分析师细分。
4. 回测进度落盘 `data/backtests/{id}/`，后端重启后 `history` 仍可查到历史记录。
5. 单日失败 fail-fast 中止、status=error、中文错误可见；cancel 可停止后续日期。
6. 全量 pytest（系统 Python）+ 前端 build + Playwright 冒烟通过（`scripts/regression.py` 全绿）。
7. 回测运行期间，不影响既有单日复盘 `/api/reviews` 与首页功能。
