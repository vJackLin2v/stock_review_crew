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

---

## 追加：每天明细表格 UI + 当天已耗时 + 重试原因/重试耗时（2026-08-16 用户确认）

### 需求（用户原话归纳）
1. 优化每天明细的表格 UI（DayDetail 现在是纯耗时条形，信息密度低）；
2. 增加「当天已耗时」统计（当天总耗时 = settle + 复盘全部尝试，含重试等待），用于统计总时间；
3. 若进行了重试，**必须给出重试原因和重试耗时**（现在只有累计数与最终错误，看不到每次尝试的失败原因与耗时）。

### 后端契约（backend/backtest.py `_run_one_day`）
新增 `day["attempts_log"]`（数组，**每次尝试一条**，成功/失败都记录，落盘）：
```python
{"attempt": 1, "ok": False, "reason": "<失败中文原因>", "elapsed_ms": 1234, "retry_sleep_ms": 2000}
{"attempt": 2, "ok": True,  "reason": "",            "elapsed_ms": 95000, "retry_sleep_ms": 0}
```
- `ok`：该次尝试成功/失败；`reason`：失败原因（成功为空串）；`elapsed_ms`：该次尝试墙钟
  （不含 sleep）；`retry_sleep_ms`：该次失败后重试等待（成功那次为 0）。
- 累计口径不变：`day.elapsed_ms == settle.elapsed_ms + review.elapsed_ms`，
  `review.elapsed_ms == sum(attempts_log.elapsed_ms) + sum(attempts_log.retry_sleep_ms)`。
- fail-fast 失败日同样写 attempts_log（3 条全失败）。
- 历史回测数据无 attempts_log → 前端缺省处理（不显示重试明细）。

### 前端（BacktestView.jsx + normalize.js + styles.css）
1. `normalizeBacktestDay` 映射 `attempts_log`（缺省安全）。
2. **DayDetail 表格化**：
   - 顶部「当天已耗时」总览（= day.elapsed_ms，含重试；与折线图口径一致），
     并拆分为 settle 撮合 + review 复盘（合计）；
   - 节点/分析师明细改为表格行（项目 / 耗时 / 占比），保留条形视觉或改纯表格
     （样式合理即可，风格与现有 bt-* 一致）；
3. **重试明细表**（`attempts_log` 存在且含 `ok=False` 条目时显示）：
   - 列：尝试次数 / 结果（成功/失败）/ 失败原因（红）/ 本次耗时 / 重试等待；
   - 让用户看到完整重试链（第 N 次失败原因 + 每次耗时）。
4. 顶部 bt-progress-meta 的「节点已耗时」等既有功能不变。

### 验收
- D1：新回测 done/failed 日 `attempts_log` 每条含 attempt/ok/reason/elapsed_ms/
  retry_sleep_ms；`sum(elapsed_ms)+sum(retry_sleep_ms) == review.elapsed_ms`。
- D2：重试过的天 attempts_log 含失败条目（reason 非空中文）；首次即成功的天单条 ok=True。
- D3：前端 DayDetail 显示「当天已耗时」总览；有重试的天显示重试明细表（原因+耗时），
  无重试的天不显示重试表；normalize 对缺省 attempts_log 安全。
- D4：npm run build 通过；msedge headless 冒烟：历史回测打开后每天明细含当天已耗时，
  重试过（attempts>1）的天显示重试明细表。

---

## 追加：LLM 超时调优 + 重试策略按失败类型区分（2026-08-16 用户确认 B1/B2）

### 背景（数据分析）
8-06/8-07 回测重试 2 次：分析师阶段 LLM 154s/279s（正常 73-91s）、报告阶段 8-06 157s。
根因：单次 LLM 调用硬超时 `llm_runtime._INVOKE_TIMEOUT=150s`——opencode 响应慢/挂起 >150s
→ TimeoutError → 整日失败 → 按 MAX_ATTEMPTS=3 重试（整日全流程重跑，浪费）。取数侧走
离线回放稳定（非取数问题）。

### B1：LLM 单次调用硬超时 150s → 200s
`src/stock_review_crew/llm_runtime.py::_INVOKE_TIMEOUT`（L19）150.0 → 200.0。
减少边缘误杀（8-07 成功那次单次调用 <150s，不受影响；慢响应 150-200s 不再被误判）。

### B2：重试策略按失败类型区分
`backend/backtest.py`：
1. 新增 `LLM_MAX_ATTEMPTS = 2`（LLM 超时类失败最多尝试 2 次即 fail-fast，避免第 3 次
   整日重跑浪费；LLM 慢是持续状态，重试收益低）。
2. 失败分类函数 `_is_llm_failure(exc)`：`isinstance(exc, TimeoutError)` → True
   （llm_runtime 硬超时抛 TimeoutError 为主 LLM 失败形态；DataValidationError/其他按
   data 类保持 MAX_ATTEMPTS=3——取数/校验失败重试可能有效：缓存热了/源恢复）。
3. `_run_one_day` 重试循环：首次失败后若判定 LLM 类 → 后续尝试上限降为
   `LLM_MAX_ATTEMPTS`；`day.attempts` / `current.attempts` 用实际生效上限
   （LLM 类日 =2，data 类日 =3）；`attempts_log` 正常记录每次尝试。
   注：现有 A4 测试 mock 抛 RuntimeError → data 类 → attempts 仍 =3，不破坏。

### 验收
- E1：`llm_runtime._INVOKE_TIMEOUT == 200.0`。
- E2：mock 单日抛 TimeoutError → 恰好尝试 2 次后 fail-fast（status=error、
  day.attempts==2、attempts_log 长度 2 全失败、last_error 含中文）；且不抛 3 次。
- E3：mock 抛非 TimeoutError（如 RuntimeError/DataValidationError）→ 保持 3 次尝试
  （现有 A3/A4 用例回归绿）。
- E4：前端 attempts 动态显示（第 N/2 或 N/3），build 通过。

---

## 追加：回测「快速模式」节点开关（跳过主持人/报告 LLM，2026-08-16 用户确认）

### 背景
8-06/8-07 回测慢：报告阶段 157s + 主持人 27s 都是 LLM 耗时大头。用户明确：
「这种回测根本不需要主持人和出报告，只要给数据+分析师判断即可，完全可以去掉
主持人和报告的 LLM」。

### 设计（lite 快速模式）
- **回测参数 `lite: bool`**（POST /api/backtest/start 支持；前端回测页 checkbox
  「快速模式（跳过主持人/报告 LLM）」）→ `run_review_day(..., lite=...)` →
  `default_initial_state(..., lite=...)` → state["lite"]。
- **host_node**：`state["lite"]` 为真 → 跳过 LLM，直接 `_rule_host` + host_analysis
  （文本标注「快速模式」而非「降级模式」；不写 LLM 降级 degraded_notes）。
- **report_node**：`state["lite"]` 为真 → 跳过 LLM，用现有 `_rule_report(state)`
  规则汇总生成 final_report + tags（`_rule_tags` 的「降级模式」标签改为「快速模式」）；
  不抛 LLM 错误；`ensure_disclaimer` 保留。
- **meta.degraded**：lite 时追加「快速模式：跳过主持人/报告 LLM 调用」——
  不含 LLM_DEGRADED_MARKERS（"LLM 不可用/LLM 失败/降级为规则"）→
  `validate_run_product` 天然放行，无需改校验。
- **分析师节点不变**（仍走 LLM——用户要分析师判断）。
- 页面复盘（trace=true）不受影响（lite 仅回测/脚本批量传）。

### 验收
- F1：lite 回测单日完成（status=done），report.json 存在且为规则汇总（文本含
  「快速模式」、不含「LLM 生成报告失败」）；meta.degraded 含「快速模式」但
  validate_run_product 通过。
- F2：lite 时 host/report 阶段**不触发 LLM**（注入 mock LLM 计数断言 host/report
  阶段零 LLM 调用；或 stage_timings host/report elapsed 极小）。
- F3：非 lite 行为不变（回归：现有 A/D/E 用例全绿）。
- F4：前端回测页有「快速模式」checkbox；build 通过；冒烟确认渲染。
