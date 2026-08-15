# 需求：统一 STOCK_REVIEW_DATA_DIR 数据根语义（reviews 根拼 /reviews）

- 状态：已确认
- 日期：2026-08-16
- 提出人：主 AI（排查 7-1 快照预热失败时发现）

## 背景（事故链）

预热 7-1 行情快照时设置 `STOCK_REVIEW_DATA_DIR=H:\stock_review_crew\data`，
脚本确认 save 成功、load 到好快照，但真实后端（无 env）读 `data/reviews/2026-07-01/`
时快照未变（仍是坏版）——好快照被写到了 `data/2026-07-01/`（少了 `reviews/` 一级）。

根因：`src/stock_review_crew/storage/reviews.py::reviews_root()` 的 env 覆盖分支是
`return Path(override)`（**把 env 直接当 reviews 根**）；而全仓其他所有消费方
（backend/backtest.py `_data_root`、accounts/{plan,storage,replay,quotes}.py、
storage/watchlist.py、backend/trades_routes.py）都是**把 env 当数据根再拼子目录**
（backtests/accounts/quotes_cache/trades…）。同一 env 两种语义 → 任何用 env 隔离的
运行（预热脚本、测试）reviews 产物与 plan/backtests 等分家。

真实后端无 env 走默认分支（`parents[3]/data/reviews`）不受影响——所以线上一直没暴露。

## 目标

统一：`STOCK_REVIEW_DATA_DIR` = **数据根**，各存储模块拼自己的子目录。

## 改动

1. `src/stock_review_crew/storage/reviews.py::reviews_root()`：
   override 分支 `return Path(override) / "reviews"`（注释说明 env=数据根语义）。
   默认分支不变。
2. 审计并同步**所有依赖旧语义的测试**：
   - `tests/*.py` 中 `monkeypatch.setenv("STOCK_REVIEW_DATA_DIR", str(...))` 的取值
     若是「reviews 目录本身」（如变量名 reviews_dir/root 直指 reviews 层），改为
     数据根（其父级）或改断言（reviews_root() 现在 = env/reviews）；
   - 断言 `reviews_root() == env值` 的地方同步；
   - 不改动其他模块（plan/quotes/backtest 等已是数据根语义）。

## 验收

- C1：`reviews_root()` 在 env 覆盖时返回 `{env}/reviews`；无 env 时默认
  `{仓库}/data/reviews`（不变）。
- C2：全量 pytest 全绿（同步后的测试自洽：env=数据根 → reviews/plan/backtests/
  quotes_cache 各归其位）。
- C3：真实后端（无 env）行为不变——save_review/market_data 仍在 data/reviews/。
- C4：预热类脚本（设置 env）不再把 reviews 产物写到错误位置。
