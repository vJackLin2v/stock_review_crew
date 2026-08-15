# 需求：回测实时诊断面板（页面可见失败/慢的原因）

- 状态：已确认
- 日期：2026-08-15
- 提出人：用户（迭代回测 UI 时提出）
- 关联：`docs/requirements-backtest-ui.md`（原回测 UI）、`docs/requirements-trading-v1.md`

## 背景（痛点）

回测跑得慢或失败时（如 8-06 连续两次失败、正在第 3 次重试），页面上只能看到
「当前日期 + 阶段名」，看不到：

1. 正在进行的节点已耗时多久、节点进度消息（如「分析师 3/5」）；
2. 当前是第几次尝试、上一次为什么失败（重试中完全黑盒）；
3. 失败重试的完整经过（哪次失败、原因、最终成功/中止）。

用户原话诉求：**「在页面上看出是什么问题」**。

## 现状（缺口确认）

- `backend/backtest.py` `_timing_cb`：仅非 analyst 事件更新
  `bt["current"] = {date, stage}`，**analyst 事件被丢弃**（进行中看不到分析师进度）；
  current 无 message/attempt/elapsed/last_error。
- `backend/backtest.py` `_run_one_day`：`day["attempts"] = attempt`（**bug**：
  attempts 应为固定上限 `MAX_ATTEMPTS`(3)，写成当前次数）。
- 前端 `BacktestView.jsx`：DayRow 失败时显示 `day.error`；但 running 中不显示
  阶段/消息/耗时；重试中不显示 attempt 与上次失败原因；无节点已耗时的实时跳动。
- GET /api/backtest/{id}：内存态直接 `_public(bt)`（引用返回），无实时 elapsed。

## 目标

回测运行中，页面实时展示「卡在哪、为什么、多久了、第几次」；
失败/中止时展示完整原因链（重试几次、每次失败原因、最终结果）。

## 接口/契约设计

### 后端 `backend/backtest.py`

1. **`current` 增强**（运行中实时可见性核心）：

   ```python
   {
     "date": date_str,          # 当前处理日期
     "stage": stage,            # settle|fetch|analysts|host|report|...
     "message": str,            # 节点进度消息（如「分析师 3/5」）
     "pct": float,              # 阶段内进度 0-100
     "attempt": int,            # 当前第几次尝试（1..MAX_ATTEMPTS）
     "attempts": int,           # 总尝试上限（MAX_ATTEMPTS=3）
     "last_error": str | None,  # 上一次失败原因（重试中可见；首次尝试为 None）
     "elapsed_ms": int,         # 节点已耗时（毫秒，GET 时实时计算）
   }
   ```

2. **`_timing_cb` 不再丢弃 analyst 事件**：所有事件（含 meta type=analyst）
   更新内存 `bt["current"]`（stage/message/pct/attempt/attempts/last_error/
   node_started_at=time.monotonic()）。落盘策略：
   - analyst 事件只更新内存 current，不 `_persist`（高频 IO 保护）；
   - 非 analyst 事件（阶段切换）照常更新 + 落盘。

3. **重试可见**：`_run_one_day` 重试循环内：
   - 每次 attempt 开始：`current = {date, stage:"review", attempt, attempts:MAX_ATTEMPTS, last_error, node_started_at}`，`day["attempt"]=attempt`；
   - 失败分支（except / 产物不合格）：`last_error = str(exc)`，
     若 `attempt < MAX_ATTEMPTS` → `current.last_error = last_error` 并落盘
     （页面可见「上次失败：…，正在重试 N/3」）；
   - 成功：`current.last_error = None`，进入下一天。

4. **修复 attempts bug**：`day["attempts"] = MAX_ATTEMPTS`（恒为 3），
   `day["attempt"] = attempt`（1..3）。

5. **统计时间含全部重试**（用户补充 2026-08-15）：`day.elapsed_ms` 与
   `day.review.elapsed_ms` 必须**累计所有尝试的墙钟耗时**——含失败尝试、
   `RETRY_SLEEP` 等待、直到成功（或 fail-fast 中止）。不再只算最后一次
   成功尝试的耗时。fail-fast 失败日也设置 `day.elapsed_ms`（累计全部 3 次尝试）。
   `day.stages` 节点明细仍为成功那次的数据（节点级无完整重试数据，可接受）。

6. **GET 实时 elapsed**：`get_backtest`/`_public` 对内存态 bt：
   若 `current.node_started_at` 存在 → 计算 `elapsed_ms =
   int((time.monotonic() - node_started_at) * 1000)` 覆盖到返回的 current
   （shallow copy，不污染内存态），并从返回对象剔除内部字段
   `node_started_at`。文件重建态（重启后查历史）无实时值 →
   elapsed_ms 可缺失，前端显示「—」。

### 前端

6. **`normalize.js` `normalizeBacktest`**：current 映射新增
   `message/pct/attempt/attempts/last_error/elapsed_ms`（缺省安全）。

7. **顶部当前状态区（bt-progress-meta 增强）**：运行中显示——
   - 当前日期 + 阶段中文名（已有）；
   - 节点已耗时：`elapsed_ms` 本地 1s tick 实时跳动（轮询快照为基准）；
   - 尝试徽标：`attempt>1` 时「第 N/3 次尝试」（橙）；
   - 上次失败警示：`last_error` 存在时黄条「上次失败：{reason}，正在重试」；
   - 节点消息：`message`（如「分析师 3/5」）。

8. **DayRow 增强**：
   - running 且 `bt.current.date === day.date`：显示当前阶段中文名 + message + 已耗时；
   - failed：显示「第 N/3 次尝试后失败」+ error 原因（已有 error 展示保留）；
   - done 且 `attempts>1`：绿色小徽标「重试 N 次」。

9. **CSS**：BacktestView 样式补 attempt 徽标 / last_error 警示条 / node 消息。

## 验收标准（可测）

- A1：GET /api/backtest/{id} 运行中 current 含全部 8 字段；elapsed_ms 为实时毫秒数
  （≥0，随节点推进增大）；终态 current 为 None 或无 elapsed_ms。
- A2：analyst 阶段运行中 current.stage=analysts 且 message 含分析师进度信息
  （单测用注入 progress 事件验证）。
- A3：单日失败重试时 current.last_error = 上次失败中文原因、attempt=当前次数；
  成功后 current.last_error=None、day.attempts=3、day.attempt 为成功那次次数。
- A4：attempts bug 修复：任何状态下 day.attempts 恒等于 MAX_ATTEMPTS。
- A5：前端 npm run build 通过；无头浏览器冒烟：回测页在运行中数据（mock）下
  渲染 attempt 徽标、last_error 警示、elapsed 跳动、节点消息。
- A6：中文文案、风格与现有 BT_TOP_STATUS/STATUS_LABEL 一致；「仅供参考」等
  既有规范不受影响。

## 约束

- **回测运行中禁止重启后端**：实现与单测全程用注入 mock（`_run_review_day_impl`/
  `_settle_impl`），不触碰运行中的 8501 进程；新代码生效需在用户确认回测结束后重启。
- current 为只读展示数据；不改变 days/stages/落盘数据结构（除 attempts 修复）。
- 不做超范围改动（不重构编排、不改变重试语义/时长）。

---

## 追加：取数校验错误消息区分「实时失败」vs「离线回放快照缺失」（2026-08-16 用户确认）

### 背景（真实事故）
7-1 复盘回测失败：`取数校验失败：index_trend数据缺失（全部数据源不可用），本次复盘终止。
请检查网络/代理/数据源后重试。`——但实际**不是网络问题**：离线回放命中了 8-15 14:03
一次失败运行留下的坏快照（该次实时取数超时 >45s，index_trend 被降级为「数据缺失」并
覆盖保存），回放照抄缺失块 → `validate_data_node`（close 关键块 index_trend 不可用）
→ DataValidationError。错误消息误导用户去查网络/代理。

### 需求
`validate_data_node` 抛错时，根据数据来源区分消息：
- **离线回放**（`snapshot.source == "离线回放"`）缺失 → 消息说明「离线回放快照中该块
  缺失（历史取数失败遗留），非本次实时取数问题；可删除 {date}/market_data.json 后
  强制实时重取」（保留 DataValidationError 类型与终止语义——快照缺失仍是真缺失，
  不静默放行）。
- **实时取数**缺失 → 保持原消息「全部数据源不可用…请检查网络/代理/数据源后重试」。

### 验收
- B1：离线回放命中坏快照（核心块 source=数据缺失）→ validate_data_node 抛
  DataValidationError，消息含「离线回放快照」与「market_data.json」。
- B2：实时取数路径缺失核心块 → 消息不变（含「全部数据源不可用」）。
- B3：快照核心块完整时回放 → 校验通过，无回归（test_offline_replay.py 全绿）。
- B4：前端透传消息展示，无需改动。
