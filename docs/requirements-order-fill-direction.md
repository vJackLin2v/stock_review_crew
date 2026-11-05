# 需求：撮合判定方向性修复（买入现价≤目标价成交 / 卖出现价≥目标价成交）

- 状态：已确认（2026-08-16 用户拍板：买卖一起修 + 按目标价成交）
- 关联：`docs/requirements-trading-v1.md`、`docs/requirements-trades-fix.md`

## 背景（用户实测发现的严重逻辑问题）

拔小弦/模拟账户撮合出现「买入被拒，原因=目标价不在区间中」：
买入 target=10，当日区间 [9.5, 9.8]（**现价整天低于目标价，价格更便宜**）却被拒绝。

根因：`src/stock_review_crew/accounts/engine.py::place_order`（L196-201）用**买卖对称**判定
「当日价格区间覆盖目标价（low ≤ target ≤ high）才成交」——把「现价更优」的场景全部误拒：

| 场景 | 现价 vs 目标价 | 当前 | 正确（限价单） |
|---|---|---|---|
| 买入 target > high | 现价低于目标价（便宜） | ❌ 误拒 | ✅ 成交 |
| 买入 target < low | 现价高于目标价（贵） | ✅ 拒 | ✅ 拒（等回调） |
| 卖出 target < low | 现价高于目标价（划算） | ❌ 误拒 | ✅ 成交 |
| 卖出 target > high | 现价低于目标价（低） | ❌ 误拒 | ✅ 成交（开盘价/最低价兜底） |

## 修复（用户已确认）

1. **判定改为方向性（限价单语义 + 卖出开盘兜底）**，`engine.py` L196-201：
   - 买入：`low > target` → 拒绝「当日未触及目标价」（现价整天高于目标价，等回调）；
   - 卖出：`high < target` → **不再拒绝**，按“开盘即卖出”处理，成交价=开盘价（开盘价缺失用最低价兜底）；
   - 其余成交。
2. **成交价**：卖出 target 在区间内/低于 low 时仍按 target 成交；卖出 target > high 时按开盘价成交（trade.price=开盘价，target_price 保留原目标价）。
3. `accounts/__init__.py` L6-7 的「成交口径（用户确认）」注释同步更新为新语义。
4. prompts.py L347 的提示文案核对（「当日价格未触及目标价不成交」语义与新判定一致，无需改；
   如需微调由实现代理判断，不得引入行为漂移）。

## 验收

- G1：买入 target > high（现价低于目标价）→ 成交（price=target，qty 按 target×成数）；
- G2：买入 target < low → 拒绝「未触及」（回归 test_accounts_engine::test_buy_reject_target_not_touched）；
- G3：卖出 target < low（现价高于目标价）→ 成交（price=target）；
- G4：卖出 target > high → 拒绝「未触及」；
- G5：涨停拒买/跌停拒卖等既有约束不变（回归 test_accounts_engine 全绿）；
- G6：全量 pytest 全绿；历史撮合数据不回填（旧结果保留，新回测用新逻辑）。

## 约束

- 只改 engine.py 判定分支 + 注释/口径文档；不改变成交价、费用、T+1、仓位计算。
- 不重启运行中的后端（用户回测/预热进行中）；新代码生效需用户确认后重启。
