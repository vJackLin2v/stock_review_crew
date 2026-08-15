# 模拟账户交易/历史持仓修复（需求文档）

> 状态：已与用户确认 3 个修复点。数据字典唯一真源 `docs/requirements.md` §六。
> 涉及：持仓天数口径、交易记录原因字段、历史持仓不聚合、收益率展示。

---

## 一、背景与 3 个问题

1. **持仓天数口径**：当前 `holding_days = (末卖日 − 首买日).days`（自然日），**不含买入当天**。
   用户口径：**买入当天算第 1 天**——今天买明天卖 = 2 天；7 月买星网锐捷 2 天后卖 = 3 天。
2. **交易记录缺原因**：买入记录无「买入原因」，卖出记录无「卖出原因」（主动/止损/止盈/超时/条件单）。
3. **历史持仓按股票聚合**：`closed_trades` 按 code 聚合，同一股票多次买卖合并成一行。
   用户要求：**按「买卖配对」生成每笔独立闭环记录**——同一股票 7 月买 2 天后卖（3 天）、8 月再买 2 天后卖（3 天）应产生**两条独立的星网锐捷历史持仓**，各自持有 3 天，不聚合。

## 二、修复方案

### 1. 持仓天数口径（含买入当天）
- `closed_trades` / `trade_stats` 的 `holding_days` 改为：**买入日算第 1 天**。
  - 即 `(卖出日 − 买入日).days + 1`（自然日）。
  - 今天买明天卖 = 2 天；7-01 买 7-03 卖 = 3 天。
- 现有数据是「不含买入日」，口径变更后历史持仓/平均持仓天数数字会整体 +1（符合用户要求）。

### 2. 交易记录原因字段
- **买入记录**：新增 `buy_reason` 字段（来源：分析师 plan 的「原因是XXX」）。
  - `plan_extractor` / `plan.py`：从分析师文本提取 `原因是(...)` 存进 plan 的 `reason` 字段；
  - `settle_plans` / `run_replay` 撮合买入成交时，把 plan 的 `reason` 写入 trade 的 `buy_reason`；
  - 无原因时 `buy_reason=None`。
- **卖出记录**：新增 `sell_reason` 字段。
  - 主动计划卖出（plan 撮合成功）：`sell_reason` = 「主动卖出」；
  - 条件单卖出（`check_conditional_orders`）：`stop_loss` / `take_profit` / `trailing_take_profit` / `timeout`（现有 `condition` 字段已有此信息，同步到 `sell_reason` 中文文案：止损/止盈/移动止盈/持有超时）。
- 交易记录序列化/存储（trades.json）带上这两个字段（None 允许，向前兼容旧数据）。

### 3. 历史持仓不聚合（按「清仓周期」）
- 重写 `closed_trades`：**按「清仓周期」**生成每笔完整闭环，不按 code 聚合、也不用 FIFO 买入批次配对：
  - 一个股票从**首次买入**开始，到**持仓清零（qty 全部卖完）**为止 = 一个完整周期，生成**一条**历史持仓记录；
  - 期间的部分卖出、加仓都算同一个周期（**只要没卖完就一直统计**，不提前闭合）；
  - 持仓天数 = 末卖日 − 首买日 + 1（含首末两天）；
  - 周期结束（清仓）后再次买入 = 新周期 = 新的独立记录。
- 例子：
  - 7-1 买 500 股 → 7-5 卖 200（未清仓）→ 8-1 卖 300（清仓）= **一条记录**，持仓天数 = 7-1 到 8-1（32 天）；
  - 星网锐捷 7 月买→全卖（3 天）= 记录 1；8 月再买→全卖（3 天）= 记录 2（两条独立）。
- 字段：`{code, name, buy_date(=周期首买日), sell_date(=周期末卖日), buy_qty, sell_qty, avg_buy_price, avg_sell_price, buy_amount, realized_pnl, holding_days(=sell_date−buy_date+1), buy_reason, sell_reason}`。
- `trade_stats` 的 `avg_holding_days` 同步用新口径（+1）。

### 4. 历史持仓表格加收益率
- 前端 `AccountsPanel.jsx` 历史持仓表格新增「收益率」列：`realized_pnl / buy_amount`（×100% 显示，红涨绿跌）。
- `closed_trades` 返回增加 `buy_amount`（或前端用 avg_buy_price×buy_qty 算），保证前端能算收益率。
- normalize.js 同步新字段。

## 三、验收标准（逐条可验证）
1. 历史持仓 `holding_days`：今天买明天卖 = 2；7-01 买 7-03 卖 = 3（含买入当天）。
2. 交易记录：买入带 `buy_reason`（来自 plan 的「原因是」），卖出带 `sell_reason`（主动/止损/止盈/移动止盈/超时）；旧数据无字段显示「—」。
3. 历史持仓：同一股票多次买卖 = 多笔独立记录，每笔独立持仓天数/盈亏/收益率，不聚合。
4. 前端历史持仓表格显示收益率列（%），红涨绿跌，None 显示「—」。
5. 全量 pytest（系统 Python）+ 前端 build + 浏览器冒烟通过；既有账户回放/合规断言不回归（`test_account_replay` / `test_r12_backtest` / `test_conditional_orders`）。

## 四、改动范围
- 后端：`src/stock_review_crew/accounts/engine.py`（closed_trades/trade_stats/place_order/check_conditional_orders）、`accounts/plan.py` + `plan_extractor.py`（reason 提取）、`accounts/replay.py`（撮合写入 buy_reason/sell_reason）。
- 前端：`AccountsPanel.jsx`（收益率列）、`normalize.js`（新字段）。
- 测试：新增/更新 `tests/test_account_replay.py` / `tests/test_r12_backtest.py` 或新增专项（配对不聚合、持仓天数 +1、reason 字段）。
