# 需求：回测断点续跑（resume）+ 产物校验失败保留行情快照

- 状态：已确认（2026-08-16 用户要求「实现断点重跑，在现有回测上续上继续跑」）
- 关联：`docs/requirements-backtest-ui.md`、`docs/requirements-backtest-live-diag.md`

## 背景（真实事故）

全量回测（4-01~8-14，98 天，bt_20260815_191441_bb45）跑到第 27 天（5-08）fail-fast error：
1. attempt 1/2：分析师 LLM 不可用/失败（opencode 当时不可用，每次超时 ~330-400s）
   → 4 位分析师降级规则引擎 → 产物校验失败；
2. 产物校验失败路径调用 `delete_review(date)` **删除整个复盘目录（含 market_data.json 好快照）**；
3. attempt 3：离线回放 miss（快照被删）→ 实时取数失败 → 保存坏快照 → 取数校验失败 → fail-fast。

**两个系统性问题**：
- A. 回测失败后无法续跑——必须整段重来（98 天 × ~1.5min ≈ 2.5h 全浪费，已完成 27 天作废）；
- B. 产物校验失败会误删行情快照，破坏离线回放（好快照丢失后回放 miss → 实时取数 → 不稳定）。

## 需求

### 1. 断点续跑（resume）
- **后端** `backend/backtest.py`：
  - `_run_loop(bt, start_idx=0)`：循环从 start_idx 开始（现有从 0）。
  - `resume_backtest(bt_id) -> {"ok", "backtest_id", "resume_from"}`：
    - 加载 bt（内存 BACKTESTS 优先，否则从 manifest+progress.json 磁盘重建）；
    - 拒绝：status=done（已完成）；已有 running/queued 回测（单 running 互斥 409）；
    - 找首个 `status != "done"` 的日索引 start_idx（跳过硬跳过 done）；无 → 409；
    - 重置 `days[start_idx:]` 为初始 pending 态（清 error/attempts_log/settle/review/stages/
      elapsed_ms/attempt/attempts）；
    - `status="running"`、`finished_at=None`、`_cancel=False`、`current={date: 首日, stage: queued}`、
      `days_done` 重算（= done 数）；
    - **不重置模拟账户**（保留已 done 天的账户状态，续跑从失败日继续撮合——方案 A 语义是
      「区间从 10 万重头」，续跑是「同一区间的延续」，账户跨天连续不能清空）；
    - 启动新线程 `_run_loop(bt, start_idx)`；落盘。
- **路由** `backend/routes_backtest.py`：`POST /api/backtest/{bt_id}/resume`。
- **前端** `BacktestView.jsx`：历史列表项（status=error/cancelled）显示「续跑」小按钮 →
  调 resume API → `beginPolling(id, initial)` 轮询。
- **api.js**：`resumeBacktest(id)`。

### 2. 产物校验失败保留行情快照（防复发）
- `src/stock_review_crew/storage/reviews.py`：`delete_review(date, time=None, *, keep_snapshot=False)`
  或新增 `delete_review_products(date)`（只删 meta/report/plan/snapshot 等产物，**保留
  market_data.json**）。`_run_one_day` 产物校验失败清理改用保留快照的版本。
- 普通删除（页面/管理操作）语义不变（keep_snapshot=False 默认删整份）。

## 验收

- H1：error 回测 resume → 返回 resume_from=失败日；进度从失败日重新跑；已 done 天保留
  （days_done 从 done 数起算）；单 running 互斥（resume 时其他 running → 409）。
- H2：done 回测 resume → 409「已完成」。
- H3：resume 后成功完成 → status=done、days 全部 done、days_done=len(days)。
- H4：产物校验失败清理后 market_data.json 仍在（离线回放不破坏）——单测断言 delete_review
  keep_snapshot 后快照文件存在。
- H5：前端历史列表 error/cancelled 项有「续跑」按钮；build 通过。
- H6：全量 pytest 全绿；真实续跑 bb45（从 5-08）验证（用户确认后执行）。

## 约束

- 不改动既有 fail-fast/重试语义；resume 只是「从失败点重跑」。
- 不重置账户（续跑连续性）；不动 start_backtest 的账户重置逻辑。
- 回测运行中不重启后端；resume 实现与单测用 mock。
