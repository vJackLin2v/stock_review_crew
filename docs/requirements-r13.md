<!-- 已合并进 requirements.md，本文档保留供追溯，勿再新增改动（2026-08-13） -->
# R13 交割单「解析明细」展示优化 — 需求分析（2026-08-09）

## 需求（用户）
1. 解析明细去掉银行转账等非股票操作（BANK_TO_SEC/SEC_TO_BANK/REPO/INTEREST/DIVIDEND/BONUS_SHARE/
   DESIGNATED_TRADE/UNKNOWN 不展示）；
2. 以**股票为单位**统计展示（每只股票的买入/卖出/盈亏）；
3. 覆盖**单向操作临界**：只有买入（未卖出）、只有卖出（期初持仓卖出）、部分平仓。

## 数据基础（已确认，无需后端改动）
`metrics.stocks` 已按股票聚合：code/name/buy_count/buy_qty/buy_amount/sell_count/sell_qty/
sell_amount/turnover/realized_pnl/unrealized_pnl/total_pnl/first_date/last_date/holding_days/
status(closed|held)。实测覆盖：买0卖N（期初卖出，301526 国际复材）、买N卖0（持仓中，
600378 昊华科技）、部分平仓（000938 紫光股份）。

## 方案（仅前端 TradesTab 展示层）
- 明细区主视图改为**股票汇总表**：代码/名称/状态（已平仓/持仓中/期初持仓卖出/持仓中未卖出）/
  买入（笔·股·额）/卖出（笔·股·额）/已实现盈亏/未实现盈亏（红涨绿跌，None「—」）；
- 每行**展开**该股票逐笔记录（仅 BUY/SELL，日期/方向/数量/价格/金额/费用）；转账等操作不出现；
- 状态口径：buy_count==0 且 sell_count>0 →「期初持仓卖出」；buy_count>0 且 sell_count==0 →
  「持仓中（未卖出）」；其余 closed →「已平仓」、held →「持仓中」；
- 未实现盈亏为 0 时按「—」展示（成本估值无意义，metrics 口径注明）。

## 约束
- 只改 frontend/src/components/TradesTab.jsx（+styles.css 少量类）；后端/复盘/聊天零改动；
- 完成后全量 pytest + 一键回测回归（17 项全绿）。
